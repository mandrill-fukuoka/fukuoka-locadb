"""重要度の材料を取り込み、点数を作り直す（順2、DB変更260）

  DATABASE_URL=... python tools/importance_import.py run [--apply] [--json out.json]   # 取得→書き込み→点数。既定は試し（最後に取り消す）
  DATABASE_URL=... python tools/importance_import.py refresh [--apply]                  # 取得せず、手元の材料で点数だけ作り直す
  DATABASE_URL=... python tools/importance_import.py revoke --apply                     # Wikidata から入れた事実をまとめて取り消す

取るもの（どちらも CC0。費用なし・AIなし。取得は直列で間隔をあける）
- OSM の wikidata / wikipedia タグで結べる地点について
  - Wikidata：日本語を含む Wikipedia の言語版の数（wikidata_sitelinks）、創設・開設年（P571。年まで）
  - Wikipedia：直近12か月の月間閲覧数の平均（wikipedia_views。人の閲覧のみ）
- 創設・開設年は、構造化データの事実として確認なしで事実カードに入れる（出どころ wikidata。revoke でまとめて取り消せる）。
  すでに承認済みの「創設・開設年」がある地点には入れない。複数の値が食い違う項目も入れない。
- 最後に refresh_spot_importance() で点数を作り直す。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date

UA = "FukuokaLocaDB/1.0 (https://github.com/mandrill-fukuoka/fukuoka-locadb; importance import)"
WD_API = "https://www.wikidata.org/w/api.php"
PV_API = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{lang}.wikipedia/all-access/user/{title}/monthly/{start}/{end}"
USER = "import:wikidata"
NOT_WIKIPEDIA = {"commonswiki", "specieswiki", "metawiki", "wikidatawiki", "mediawikiwiki", "foundationwiki", "outreachwiki",
                 "incubatorwiki", "sourceswiki", "wikimaniawiki", "testwiki", "test2wiki", "betawikiversity"}
QID = re.compile(r"^Q[1-9][0-9]*$")
WPTAG = re.compile(r"^([a-z][a-z-]{1,7}):(.+)$")
GAP = {"wd": 1.1, "pv": 0.3}
_last = {}


def http_json(url: str, kind: str):
    """間隔をあけて取得する（kind ごとに直列）。429/5xx は待って再試行。404 は None"""
    for attempt in range(4):
        wait = GAP[kind] - (time.time() - _last.get(kind, 0))
        if wait > 0:
            time.sleep(wait)
        _last[kind] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            print("retry", attempt, e.code, url[:90], file=sys.stderr, flush=True)
        except Exception as e:
            print("retry", attempt, type(e).__name__, str(e)[:80], file=sys.stderr, flush=True)
        time.sleep(5 * (attempt + 1))
    raise RuntimeError("取得に失敗: " + url[:120])


# ---- 解析（ネットワーク不要。テスト対象）----

def build_targets(rows: list[tuple]) -> tuple[dict, dict]:
    """(spot_id, wikidata タグ, wikipedia タグ) の一覧から、{Q番号: [spot_id]} と {(言語, 記事名): [spot_id]} を作る"""
    by_q, by_title = defaultdict(set), defaultdict(set)
    for sid, q, wp in rows:
        q = (q or "").strip()
        if QID.match(q):
            by_q[q].add(sid)
        m = WPTAG.match((wp or "").strip())
        if m:
            by_title[(m.group(1), m.group(2).replace("_", " ").strip())].add(sid)
    return ({k: sorted(v) for k, v in by_q.items()}, {k: sorted(v) for k, v in by_title.items()})


def year_from_claim(claims: dict) -> int | None:
    """P571（設立・創設日）の年。年以上の精度（precision>=9）で、西暦600年〜今年の値が1つに決まるときだけ返す"""
    years = set()
    for c in claims.get("P571", []):
        if c.get("rank") == "deprecated":
            continue
        v = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if not isinstance(v, dict) or int(v.get("precision") or 0) < 9:
            continue
        m = re.match(r"^\+(\d{1,4})-", v.get("time") or "")
        if m and 600 <= int(m.group(1)) <= date.today().year:
            years.add(int(m.group(1)))
    return years.pop() if len(years) == 1 else None


def parse_entity(ent: dict) -> dict:
    sl = ent.get("sitelinks") or {}
    editions = [k for k in sl if k.endswith("wiki") and k not in NOT_WIKIPEDIA]
    return {"sitelinks": len(editions), "jawiki": (sl.get("jawiki") or {}).get("title"),
            "year": year_from_claim(ent.get("claims") or {})}


def views_mean(items: list[dict], months: int = 12) -> float:
    """月間閲覧数の平均（データのない月は0として months で割る）"""
    return round(sum(int(i.get("views") or 0) for i in items) / months, 1)


def month_range(today: date, months: int = 12) -> tuple[str, str]:
    """直近の完了した月から遡って months か月。(YYYYMM0100, YYYYMM0100)"""
    y, m = today.year, today.month - 1
    if m == 0:
        y, m = y - 1, 12
    end = f"{y}{m:02d}0100"
    sy, sm = y, m - (months - 1)
    while sm <= 0:
        sy, sm = sy - 1, sm + 12
    return f"{sy}{sm:02d}0100", end


# ---- 取得 ----

def fetch_wikidata(qids: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(qids), 50):
        part = qids[i:i + 50]
        url = WD_API + "?" + urllib.parse.urlencode({"action": "wbgetentities", "ids": "|".join(part), "props": "sitelinks|claims",
                                                      "format": "json", "formatversion": "2"})
        js = http_json(url, "wd") or {}
        for q, ent in (js.get("entities") or {}).items():
            if "missing" not in ent:
                out[q] = parse_entity(ent)
    return out


def fetch_views(titles: list[tuple[str, str]], today: date) -> dict[tuple[str, str], float]:
    start, end = month_range(today)
    out = {}
    for n, (lang, title) in enumerate(titles, 1):
        url = PV_API.format(lang=lang, title=urllib.parse.quote(title.replace(" ", "_"), safe=""), start=start, end=end)
        js = http_json(url, "pv")
        if js and js.get("items"):
            out[(lang, title)] = views_mean(js["items"])
        if n % 100 == 0:
            print(f"閲覧数 {n}/{len(titles)}", file=sys.stderr, flush=True)
    return out


# ---- 書き込み ----

def load_targets(conn) -> list[tuple]:
    return conn.execute("""
        select distinct on (s.id) s.id, f.tags->>'wikidata', f.tags->>'wikipedia'
          from spots s join spot_features sf on sf.spot_id = s.id join features f on f.key = sf.feature_key
         where s.data_status <> 'archived' and (f.tags ? 'wikidata' or f.tags ? 'wikipedia')
         order by s.id, (sf.role = 'primary') desc, sf.feature_key""").fetchall()


def run(conn, today: date | None = None, fetch=True) -> dict:
    today = today or date.today()
    stats = {"fetched": fetch}
    if fetch:
        rows = load_targets(conn)
        by_q, by_title = build_targets(rows)
        stats.update(spots_with_tags=len(rows), qids=len(by_q), titles_from_tags=len(by_title))
        wd = fetch_wikidata(sorted(by_q))
        stats["wikidata_found"] = len(wd)
        # 日本語版の記事名は Wikidata から補う（wikipedia タグのない地点の閲覧数）
        for q, info in wd.items():
            if info["jawiki"]:
                by_title.setdefault(("ja", info["jawiki"]), [])
                by_title[("ja", info["jawiki"])] = sorted(set(by_title[("ja", info["jawiki"])]) | set(by_q[q]))
        views = fetch_views(sorted(by_title), today)
        stats["pages_with_views"] = len(views)
        sig = {}     # (spot_id, kind) -> (value, ref)
        for q, info in wd.items():
            for sid in by_q[q]:
                sig[(sid, "wikidata_sitelinks")] = (float(info["sitelinks"]), f"wikidata:{q}")
        for (lang, title), v in views.items():
            for sid in by_title[(lang, title)]:
                if (sid, "wikipedia_views") not in sig or v > sig[(sid, "wikipedia_views")][0]:
                    sig[(sid, "wikipedia_views")] = (v, f"{lang}.wikipedia:{title}")
        for kind in ("wikidata_sitelinks", "wikipedia_views"):
            ids = [sid for (sid, k) in sig if k == kind]
            conn.execute("delete from spot_signals where kind = %s and spot_id <> all(%s)", (kind, ids or [0]))
        for (sid, kind), (v, ref) in sig.items():
            conn.execute("""insert into spot_signals (spot_id, kind, value, source_ref) values (%s, %s, %s, %s)
                            on conflict (spot_id, kind) do update set value = excluded.value, source_ref = excluded.source_ref, fetched_at = now()""",
                         (sid, kind, v, ref))
        stats["signals"] = len(sig)
        # 事実カード：創設・開設年
        have = {r[0] for r in conn.execute("select spot_id from spot_facts where status = 'approved' and item = '創設・開設年'").fetchall()}
        items, skipped_existing = [], 0
        for q, info in wd.items():
            if not info["year"]:
                continue
            for sid in by_q[q]:
                if sid in have:
                    skipped_existing += 1
                    continue
                items.append({"spot_id": sid, "item": "創設・開設年", "value": f"{info['year']}年", "note": "Wikidata の設立・創設日（P571）",
                              "source_kind": "wikidata", "source_url": f"https://www.wikidata.org/wiki/{q}",
                              "source_title": f"Wikidata {q}", "license": "CC0 1.0"})
        added = skipped = 0
        rejected = []
        for i in range(0, len(items), 200):
            r = conn.execute("select facts_import(%s::jsonb, %s)", (json.dumps(items[i:i + 200], ensure_ascii=False), USER)).fetchone()[0]
            added += r["added"]; skipped += r["skipped"]; rejected += r["rejected"]
        stats.update(facts_candidates=len(items), facts_added=added, facts_skipped=skipped, facts_skipped_existing=skipped_existing,
                     facts_rejected=rejected[:5], facts_rejected_n=len(rejected))
    stats["scored"] = conn.execute("select refresh_spot_importance()").fetchone()[0]
    stats.update(summary(conn))
    return stats


def summary(conn) -> dict:
    d = conn.execute("""select count(*) filter (where score > 0), count(*) filter (where score >= 0.1), count(*) filter (where score >= 0.3),
                               count(*) filter (where score >= 0.5), count(*) filter (where hidden_score > 0), count(*)
                          from spot_importance""").fetchone()
    top = conn.execute("""select s.name, i.score, i.hidden_score from spot_importance i join spots s on s.id = i.spot_id
                           order by i.score desc, s.id limit 15""").fetchall()
    hid = conn.execute("""select s.name, i.score, i.hidden_score from spot_importance i join spots s on s.id = i.spot_id
                           order by i.hidden_score desc, s.id limit 10""").fetchall()
    return {"dist": {"総数": d[5], "点数>0": d[0], "0.1以上": d[1], "0.3以上": d[2], "0.5以上": d[3], "穴場度>0": d[4]},
            "top": [[n, round(a, 3), round(b, 3)] for n, a, b in top], "hidden_top": [[n, round(a, 3), round(b, 3)] for n, a, b in hid]}


def render(stats: dict, mode: str) -> str:
    L = [f"## 重要度の取り込み（{mode}）", ""]
    L += [f"- {k}：{v}" for k, v in stats.items() if k not in ("dist", "top", "hidden_top", "facts_rejected")]
    if stats.get("facts_rejected"):
        L += ["", f"弾かれた事実（先頭5件）：{stats['facts_rejected']}"]
    L += ["", "### 点数の分布", ""] + [f"- {k}：{v}" for k, v in stats["dist"].items()]
    L += ["", "### 重要度の上位15", "", "| 地点 | 重要度 | 穴場度 |", "|---|---:|---:|"] + [f"| {n} | {a} | {b} |" for n, a, b in stats["top"]]
    L += ["", "### 穴場度の上位10", "", "| 地点 | 重要度 | 穴場度 |", "|---|---:|---:|"] + [f"| {n} | {a} | {b} |" for n, a, b in stats["hidden_top"]]
    return "\n".join(L)


def main() -> int:
    import psycopg
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "refresh", "revoke"])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    mode = "apply" if a.apply else "dry_run"
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        if a.cmd == "revoke":
            n = conn.execute("select revoke_facts_by_source('wikidata', %s, %s)", (USER, "Wikidata の事実をまとめて取り消し")).fetchone()[0]
            conn.execute("select refresh_spot_importance()")
            print(f"Wikidata の事実を取り消し：{n} 件（{mode}）")
        else:
            stats = run(conn, fetch=(a.cmd == "run"))
            print(render(stats, mode))
            if a.json:
                open(a.json, "w", encoding="utf-8").write(json.dumps(stats, ensure_ascii=False, indent=1))
        if a.apply:
            conn.commit()
        else:
            conn.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main())
