"""写真候補のふるい直し（順4。作業方針 5章）：規則を過去の採否に当てて採用率を出す／確認のうえ候補を整える

  python -m tools.photo_rescreen analyze --out DIR [--no-categories]    読み取りだけ（DBは SELECT のみ。Commons の API を読む）
  python -m tools.photo_rescreen apply                                   未決の候補だけを整える（下記）。人が決めたものは変えない
  python -m tools.photo_rescreen reject-sign [--yes]                     未決で、説明文・題名に看板・標識・地図などの語がある候補を、理由 sign でまとめて不採用にする
                                                                         （過去の採用率 0%＝0/50。利用者の指示 2026-11-20。decided_by = bulk:sign。確認画面の「候補に戻す」で戻せる）

調べること
  1. 照合の拡張：地点名（別名を含む）のローマ字・英語名、Commons のカテゴリ名での照合を足す（locadb/photo_match.py）。
     足した照合で新しく一致するものが、過去の採否でどれだけ採用されているか（採用率）
  2. 同じ写真が複数の地点に出る問題：同じ出典ページの候補を、一致→近さの順に並べ、先頭とそれ以外の採用率
  3. 現在の「見込み低」の規則ごとの採用率（一括不採用の候補にしてよい群があるか）
apply の内容（未決の候補だけ）
  - match_kind（ja / romaji / category）と categories を保存する。name_match は、日本語の別名での一致（ja）のときだけ true にする
    （ローマ字・英語名・カテゴリ名での一致は過去の採用率が61%で、見込み高の基準を満たさないため。判定は photo_suggest が match_kind で「どちらでもない」に上げる）
  - 同じ写真が複数の地点にあるとき、先頭（採用済み→一致→近さの順）が一致し、自分は一致しない2か所目以降だけを「別の場所」で不採用にする
    （decided_by = auto:dedup。過去の採用率は6%。「確認画面の候補に戻す」（reset）でいつでも戻せる）。ほかの2か所目以降（先頭も一致しない）は触れない
採用は人が決める（自動採用はしない）。画像は保存しない。
"""
import argparse
import collections
import csv
import re
import time
from pathlib import Path

import requests

from locadb.db import connect, job
from locadb.photo_match import match_kind

API = "https://commons.wikimedia.org/w/api.php"
UA = "fukuoka-locadb/2.0 (https://github.com/mandrill-fukuoka/fukuoka-locadb; location research)"
INTERVAL_S = 1.1
SIGN = re.compile(r"(看板|標識|案内|駅名標|地図|ポスター|\bsigns?\b|\bplaque\b|\bmap\b|\bposter\b)", re.I)
MIN_N = 8

SQL = """
select c.id, c.spot_id, c.source, c.file_title, c.page_url, c.description, c.dist_m, c.name_match, c.status, c.reject_reason, {cats},
       s.name as spot_name, s.alt_names, s.category
  from spot_photo_candidates c join spots s on s.id = c.spot_id
 where s.data_status <> 'archived'
"""


def fetch_categories(titles: list[str], session=requests, sleep=time.sleep) -> dict[str, list[str]]:
    """Commons のカテゴリ名（隠しカテゴリは除く）。50件ずつ、直列・間隔つき。取れなかった題名は含めない"""
    out: dict[str, list[str]] = {}
    for i in range(0, len(titles), 50):
        chunk = titles[i:i + 50]
        params = {"action": "query", "format": "json", "formatversion": "2", "prop": "categories", "titles": "|".join(chunk),
                  "cllimit": "max", "clshow": "!hidden"}
        for attempt in range(3):
            try:
                r = session.get(API, params=params, timeout=30, headers={"User-Agent": UA})
                if r.status_code == 200:
                    for pg in ((r.json() or {}).get("query") or {}).get("pages", []):
                        out[pg.get("title")] = [re.sub(r"^Category:", "", c["title"]) for c in pg.get("categories", [])]
                    break
            except (requests.RequestException, ValueError):
                pass
            sleep(3 * (attempt + 1))
        sleep(INTERVAL_S)
    return out


def names_of(r) -> list[str]:
    return [r["spot_name"], *(r["alt_names"] or [])]


def old_rule(r, name_match: bool) -> str:
    """現在の photo_suggest の判定の理由（どの規則で low になるか）。high / unknown / low:○○"""
    text = (r["description"] or "") + " " + (r["file_title"] or "")
    if SIGN.search(text):
        return "low:看板・標識・地図"
    if r["source"] == "commons" and name_match:
        return "high"
    if r["source"] == "commons" and (r["dist_m"] or 0) >= 30:
        return "low:名前不一致で30m以上"
    if r["source"] == "commons" and r["category"] in ("heritage", "school", "civic"):
        return "low:史跡・学校・官公庁"
    if r["source"] == "mapillary" and r["category"] is not None and r["category"] != "commercial_facility":
        return "low:Mapillary"
    return "unknown"


def rate(a, r):
    n = a + r
    return f"{a / n:.0%}{'（少数）' if n < MIN_N else ''}" if n else "-"


def dup_order(rows):
    """同じ出典ページの候補を、採用済み → 一致 → 近さの順に並べる。返り値：{id: 順位（1が先頭）}。2件以上あるページだけ"""
    g = collections.defaultdict(list)
    for r in rows:
        g[r["page_url"]].append(r)
    rank = {}
    for url, rs in g.items():
        if len(rs) < 2:
            continue
        rs.sort(key=lambda r: (r["status"] != "adopted", not r["new_match"], r["dist_m"] if r["dist_m"] is not None else 1e9, r["id"]))
        for i, r in enumerate(rs):
            rank[r["id"]] = i + 1
    return rank


def dedup_targets(rows, rank):
    """不採用にする候補：同じ写真の2か所目以降で、自分は一致せず、先頭が一致している（先頭が採用済みか未決）。決定済みのものは対象外"""
    top = {r["page_url"]: r for r in rows if rank.get(r["id"]) == 1}
    return [r for r in rows if r["source"] == "commons" and r["status"] == "candidate" and rank.get(r["id"], 1) > 1 and not r["new_match"]
            and top.get(r["page_url"], {}).get("new_match") and top[r["page_url"]]["status"] in ("adopted", "candidate")]


def evaluate(rows, cats):
    for r in rows:
        r["categories"] = cats.get(r["file_title"]) or r["categories"] or []
        r["kind"] = match_kind(names_of(r), r["file_title"], r["description"], r["categories"]) if r["source"] == "commons" else None
        r["new_match"] = bool(r["name_match"] or r["kind"])
    return dup_order(rows)


def report(rows, rank):
    commons = [r for r in rows if r["source"] == "commons"]
    dec = lambda rs: (sum(r["status"] == "adopted" for r in rs), sum(r["status"] == "rejected" for r in rs), sum(r["status"] == "candidate" for r in rs))
    md = [f"Commons の候補 {len(commons)}件（採用 {dec(commons)[0]}・不採用 {dec(commons)[1]}・未決 {dec(commons)[2]}）／Mapillary {len(rows) - len(commons)}件\n"]
    md.append("### 1. 照合の拡張（現在の名前一致 × 新しい照合）\n| 現在の名前一致 | 新しい照合 | 採用 | 不採用 | 採用率（決定済み） | 未決 |\n|---|---|---|---|---|---|")
    g = collections.defaultdict(list)
    for r in commons:
        g[("一致" if r["name_match"] else "不一致", r["kind"] or "なし")].append(r)
    for k, rs in sorted(g.items()):
        a, rj, u = dec(rs)
        md.append(f"| {k[0]} | {k[1]} | {a} | {rj} | {rate(a, rj)} | {u} |")
    md.append("\n（「不一致 × romaji / category」が、新しく一致になるもの。採用率が高ければ見込み高に上げてよい）\n")
    md.append("### 2. 同じ写真が複数の地点に出る\n")
    multi = [r for r in commons if r["id"] in rank]
    pages = len({r["page_url"] for r in multi})
    md.append(f"同じ出典ページの候補が2地点以上にあるもの：{pages}ページ・{len(multi)}件（うち2か所目以降 {sum(1 for r in multi if rank[r['id']] > 1)}件、未決の2か所目以降 "
              f"{sum(1 for r in multi if rank[r['id']] > 1 and r['status'] == 'candidate')}件）\n")
    md.append("| 順位 | 採用 | 不採用 | 採用率（決定済み） | 未決 |\n|---|---|---|---|---|")
    for label, f in (("先頭（採用済み→一致→近さ）", lambda r: rank[r["id"]] == 1), ("2か所目以降", lambda r: rank[r["id"]] > 1)):
        a, rj, u = dec([r for r in multi if f(r)])
        md.append(f"| {label} | {a} | {rj} | {rate(a, rj)} | {u} |")
    narrow = dedup_targets(rows, rank)
    md.append(f"\napply で不採用にする候補（2か所目以降で、自分は不一致・先頭が一致）：未決 {len(narrow)}件\n")
    md.append("\n### 3. 現在の判定の規則ごと（新しい照合を入れる前／入れた後）\n| 規則 | 採用 | 不採用 | 採用率（決定済み） | 未決 | 新しい照合で一致に変わる未決 |\n|---|---|---|---|---|---|")
    g = collections.defaultdict(list)
    for r in rows:
        g[old_rule(r, bool(r["name_match"]))].append(r)
    for k, rs in sorted(g.items()):
        a, rj, u = dec(rs)
        moved = sum(1 for r in rs if r["status"] == "candidate" and r["kind"] and not r["name_match"])
        md.append(f"| {k} | {a} | {rj} | {rate(a, rj)} | {u} | {moved} |")
    return "\n".join(md)


def load(conn):
    """DB変更270（categories 列）の前でも読めるようにする（analyze は270の前に実行できる）"""
    has = conn.execute("select 1 from information_schema.columns where table_name = 'spot_photo_candidates' and column_name = 'categories'").fetchone()
    return [dict(r) for r in conn.execute(SQL.replace("{cats}", "c.categories" if has else "null::text[] as categories")).fetchall()]


def cmd_analyze(a):
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        rows = load(conn)
    cats = {} if a.no_categories else fetch_categories(sorted({r["file_title"] for r in rows if r["source"] == "commons"}))
    rank = evaluate(rows, cats)
    md = report(rows, rank)
    (out / "summary.md").write_text(md, encoding="utf-8")
    with open(out / "rescreen.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["状態", "理由", "地点", "ファイル", "距離m", "いまの一致", "新しい照合", "同じ写真の順位", "カテゴリ"])
        for r in rows:
            if r["source"] == "commons" and (r["kind"] or r["id"] in rank):
                w.writerow([r["status"], r["reject_reason"] or "", r["spot_name"], r["file_title"], round(r["dist_m"] or 0), "○" if r["name_match"] else "",
                            r["kind"] or "", rank.get(r["id"], ""), " / ".join(r["categories"][:3])])
    print(md)


def cmd_apply(a):
    with connect() as conn, job(conn, "photo_rescreen", None) as stats:
        rows = load(conn)
        cats = fetch_categories(sorted({r["file_title"] for r in rows if r["source"] == "commons"}))
        rank = evaluate(rows, cats)
        todo = [r for r in rows if r["status"] == "candidate" and r["source"] == "commons"]
        with conn.cursor() as cur:
            for r in todo:
                cur.execute("update spot_photo_candidates set match_kind = %s, categories = %s, name_match = name_match or %s, rescreened_at = now() where id = %s",
                            (r["kind"], r["categories"], r["kind"] == "ja", r["id"]))
            dup = dedup_targets(rows, rank)
            for r in dup:
                cur.execute("update spot_photo_candidates set status = 'rejected', reject_reason = 'wrong_place', decided_by = 'auto:dedup', decided_at = now() "
                            "where id = %s and status = 'candidate'", (r["id"],))
        stats.update(candidates=len(todo), newly_matched=sum(1 for r in todo if r["kind"] and not r["name_match"]), dedup_rejected=len(dup))
        if not a.yes:
            conn.rollback()
        else:
            conn.commit()
        print({**stats, "applied": a.yes})


REJECT_SIGN_SQL = """
select c.id from spot_photo_candidates c join spots s on s.id = c.spot_id and s.data_status <> 'archived'
 where c.status = 'candidate'
   and photo_suggest(c.source, c.name_match, c.description, c.file_title, s.category, c.dist_m::numeric, c.match_kind) = 'low'
   and photo_suggest_reason(c.source, c.description, c.file_title) = 'sign'
 order by c.id
"""


def cmd_reject_sign(a):
    """photo_decide_bulk（確認画面の一括不採用と同じ関数）で、見込み低のうち理由が sign のものだけを不採用にする"""
    with connect() as conn, job(conn, "photo_reject_sign", None) as stats:
        ids = [r["id"] for r in conn.execute(REJECT_SIGN_SQL).fetchall()]
        stats["targets"] = len(ids)
        if ids:
            res = conn.execute("select photo_decide_bulk(%s, 'reject', 'bulk:sign', 'sign') r", (ids,)).fetchone()["r"]
            stats.update(decided=res.get("decided"), skipped=res.get("skipped"))
        if not a.yes:
            conn.rollback()
        else:
            conn.commit()
        print({**stats, "applied": a.yes})


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("analyze"); p.add_argument("--out", default="photo-rescreen-out"); p.add_argument("--no-categories", action="store_true")
    q = sub.add_parser("apply"); q.add_argument("--yes", action="store_true", help="付けないときは試し実行（最後に取り消す）")
    r = sub.add_parser("reject-sign"); r.add_argument("--yes", action="store_true", help="付けないときは試し実行（最後に取り消す）")
    a = ap.parse_args(argv)
    {"analyze": cmd_analyze, "apply": cmd_apply, "reject-sign": cmd_reject_sign}[a.cmd](a)


if __name__ == "__main__":
    main()
