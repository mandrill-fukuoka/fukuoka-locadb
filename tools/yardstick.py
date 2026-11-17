"""評価の物差し（順1）：場所検索の地点リスト（カット）と、流れ検索の確認用シーン文を、同じ手順で採点する

  python tools/yardstick.py interp [--write]          # シーン文の解釈（DB不要。ci でも使う）。--write で基準を書き直す
  DATABASE_URL=... python tools/yardstick.py run [--json out.json] [--write-baseline]

採点の考え方（2026-11-17）
- 場所検索：data/yardstick/place_cuts.json の各カットが「DBに名前（別名を含む）で見つかるか」を ok / ng で数える。
- 流れ検索：data/flow_eval/scenes.txt の各シーン文で build_routes（画面と同じ呼び方）を呼び、
  1位の流れが、読み取った景観の順をすべて含んでいれば ok。適合度（match）の平均も出す。
- 基準（baseline）：場所検索は place_cuts.json の baseline、流れ検索は data/yardstick/flow_baseline.json。
  基準で ok だったものが ng になったら「後退」。後退が1つでもあるか、適合度の平均が基準より 0.03 以上下がれば、終了コード 1（赤）。
- 読み取りだけ：DB へは SELECT と build_routes（一時表を作る）だけ。最後に rollback。
- 注意：本番DBを見るので、PR の SQL がまだ本番に入っていなければ、PR の効果は測れない（db-migrate の後に測る）。
  PR ごとに必ず動くのは、シーン文の解釈の基準（interp）とこのファイルの採点部分のテスト（tests/test_yardstick.py）。
"""
import argparse
import json
import os
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data" / "yardstick"
CUTS = DATA / "place_cuts.json"
FLOW_BASE = DATA / "flow_baseline.json"
INTERP_BASE = DATA / "scene_interpretation.json"
SCENES = ROOT / "data" / "flow_eval" / "scenes.txt"
MATCH_DROP = 0.03        # 適合度の平均がこれ以上下がったら後退とみなす


def load_scenes(path: Path = SCENES) -> list[str]:
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.lstrip().startswith("#")]


# ---- 場所検索（カット）----

def check_cut(conn, cut: dict) -> dict:
    """カットに当たる地点を探す。名前か別名が pattern に合い、categories があればその種類のもの（アーカイブ済みは除く）"""
    cats = cut.get("categories")
    rows = conn.execute(
        """select id, name, category from spots
            where data_status <> 'archived'
              and (name ~ %(p)s or exists (select 1 from unnest(alt_names) a where a ~ %(p)s))
              and (%(c)s::text[] is null or category = any(%(c)s::text[]))
            order by id limit 5""", {"p": cut["pattern"], "c": cats}).fetchall()
    rows = [tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in rows]
    return {"no": cut["no"], "name": cut["name"], "status": "ok" if rows else "ng",
            "found": [{"id": r[0], "name": r[1], "category": r[2]} for r in rows]}


# ---- 流れ検索（シーン）----

def covers(route: dict, sequence: list[str]) -> bool:
    """流れの地点の group が、読み取った景観の順（sequence）を、順番どおりに（間に別の地点があってもよい）含むか"""
    it = iter(s.get("group") for s in route.get("spots", []))
    return all(any(g == want for g in it) for want in sequence)


def judge_scene(q: dict, routes: list[dict]) -> dict:
    top = routes[0] if routes else None
    ok = bool(top) and covers(top, q.get("sequence") or [])
    return {"status": "ok" if ok else "ng", "routes": len(routes),
            "match": round(float(top.get("match") or 0), 3) if top else 0.0,
            "spots": [s["name"] for s in top["spots"]] if top else []}


# ---- 基準との比べ方 ----

def compare(current: dict[str, str], baseline: dict[str, str]) -> dict:
    """current / baseline は {キー: 'ok'|'ng'}。基準が ok で今 ng を後退、基準が ng で今 ok を改善とする。基準が無いもの（None）は数えない"""
    reg = sorted(k for k, v in baseline.items() if v == "ok" and current.get(k) != "ok")
    imp = sorted(k for k, v in baseline.items() if v == "ng" and current.get(k) == "ok")
    new = sorted(k for k, v in baseline.items() if v is None and k in current)
    return {"regressions": reg, "improvements": imp, "unmeasured": new}


def match_mean(results: list[dict]) -> float:
    vals = [r["match"] for r in results if r["routes"]]
    return round(statistics.mean(vals), 3) if vals else 0.0


# ---- シーン文の解釈（DB不要）----

def interpret_all(scenes: list[str]) -> dict:
    from tools.flow_eval import interpret
    out = {}
    for s in scenes:
        q = interpret(s)
        out[s] = {k: q.get(k) for k in ("sequence", "tags", "travel_mode", "area", "near", "kinds")}
    return out


def cmd_interp(a) -> int:
    cur = interpret_all(load_scenes())
    if a.write:
        INTERP_BASE.write_text(json.dumps(cur, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"解釈の基準を書きました（{len(cur)} 本）")
        return 0
    base = json.loads(INTERP_BASE.read_text(encoding="utf-8"))
    diff = [s for s in cur if cur[s] != base.get(s)] + [s for s in base if s not in cur]
    for s in diff:
        print(f"解釈が変わった：{s}\n  基準 {base.get(s)}\n  今　 {cur.get(s)}")
    print(f"シーン文の解釈：{len(cur) - len(diff)}/{len(cur)} が基準と同じ")
    return 1 if diff else 0


# ---- 本番DBでの採点 ----

def cmd_run(a) -> int:
    import psycopg
    from psycopg.rows import dict_row
    from tools.flow_eval import search_scene
    cuts = json.loads(CUTS.read_text(encoding="utf-8"))["cuts"]
    scenes = load_scenes()
    fb = json.loads(FLOW_BASE.read_text(encoding="utf-8")) if FLOW_BASE.exists() else {"scenes": {}, "match_mean": None}
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as c:
        place = [check_cut(c, cut) for cut in cuts]
        flow = []
        for i, sc in enumerate(scenes, 1):
            q, rs, ms = search_scene(c, sc)
            flow.append({"no": i, "scene": sc, "ms": ms, **judge_scene(q, rs)})
        c.rollback()
    p_cur = {str(r["no"]): r["status"] for r in place}
    p_base = {str(cut["no"]): cut.get("baseline") for cut in cuts}
    f_cur = {r["scene"]: r["status"] for r in flow}
    f_base = {k: v.get("status") for k, v in fb["scenes"].items()}
    pc, fc = compare(p_cur, p_base), compare(f_cur, f_base)
    mm = match_mean(flow)
    drop = fb.get("match_mean") is not None and mm < fb["match_mean"] - MATCH_DROP
    n_ok = lambda rs: sum(r["status"] == "ok" for r in rs)
    L = ["## 評価の物差し", "",
         f"- 場所検索：{n_ok(place)} / {len(place)} カットがDBで見つかる（元の表は {json.loads(CUTS.read_text(encoding='utf-8'))['total_cuts']} カット中、ここに収録は {len(place)}）",
         f"- 流れ検索：{n_ok(flow)} / {len(flow)} シーンで1位の流れが景観の順を満たす。適合度の平均 {mm}"
         + (f"（基準 {fb['match_mean']}）" if fb.get("match_mean") is not None else "（基準なし）"), ""]
    names = {str(r["no"]): r["name"] for r in place}
    for title, cmpres, label in (("場所検索", pc, lambda k: f"{k} {names.get(k, '')}"), ("流れ検索", fc, lambda k: k)):
        if cmpres["regressions"]:
            L += [f"### 後退（{title}）", ""] + [f"- {label(k)}" for k in cmpres["regressions"]] + [""]
        if cmpres["improvements"]:
            L += [f"### 改善（{title}）", ""] + [f"- {label(k)}" for k in cmpres["improvements"]] + [""]
        if cmpres["unmeasured"]:
            L += [f"- {title}：基準が未測定のもの {len(cmpres['unmeasured'])} 件（--write-baseline で基準を作る）", ""]
    if drop:
        L += [f"### 後退：適合度の平均が基準 {fb['match_mean']} から {mm} に下がりました", ""]
    L += ["### ng のカット・シーン", ""] + [f"- カット {r['no']} {r['name']}" for r in place if r["status"] == "ng"] \
         + [f"- シーン {r['no']} {r['scene']}（{r['routes']} 件）" for r in flow if r["status"] == "ng"]
    print("\n".join(L))
    if a.json:
        Path(a.json).write_text(json.dumps({"place": place, "flow": flow, "match_mean": mm}, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.write_baseline:
        doc = json.loads(CUTS.read_text(encoding="utf-8"))
        for cut in doc["cuts"]:
            cut["baseline"] = p_cur[str(cut["no"])]
        CUTS.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        FLOW_BASE.write_text(json.dumps({"match_mean": mm, "scenes": {r["scene"]: {"status": r["status"], "match": r["match"]} for r in flow}},
                                        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("\n基準を書きました（place_cuts.json・flow_baseline.json）")
        return 0
    return 1 if (pc["regressions"] or fc["regressions"] or drop) else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("interp"); i.add_argument("--write", action="store_true"); i.set_defaults(f=cmd_interp)
    r = sub.add_parser("run"); r.add_argument("--json"); r.add_argument("--write-baseline", action="store_true"); r.set_defaults(f=cmd_run)
    a = ap.parse_args()
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
