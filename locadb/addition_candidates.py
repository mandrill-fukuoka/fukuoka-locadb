"""追加候補の収集：DBにまだない「名前のある場所」を、Wikipedia（日本語）と Wikimedia Commons から候補として集める（AIなし・費用ゼロ）

  python -m locadb.addition_candidates --dry-run [--out DIR]    読み取りのみ。候補の一覧（CSV）を出すだけ
  python -m locadb.addition_candidates --apply                  候補を spot_additions に保存する（公開はしない）

決まり
  - 福岡市7区の中（areas の境界）にあるものだけ。近く150m以内に既存の地点があるもの、近く1km以内に同じ名前（似た名前）の地点があるものは出さない
  - Wikipedia：座標つきの記事の「名前・座標・記事のURL」だけを保存する（本文は保存しない）。市町村・区・丁目などの行政の項目は出さない
  - Commons：近く100m以内に既存の地点がない場所に、写真が5枚以上集まる100m四方を「名称未設定」の候補にする（名前は人が付ける）
  - 外部への取得は1秒以上の間隔で直列。User-Agent に連絡先。検索エンジンの画像は取らない。
  - 候補は人が確認画面で「採用」したときだけ地点になる（addition_decide）。決めた候補は上書きしない
"""
import argparse
import csv
import json
import math
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests

from .db import connect, job

UA = "fukuoka-locadb/2.0 (https://github.com/mandrillfuk-dev/fukuoka-locadb; location research)"
INTERVAL_S = 1.1
BBOX = (33.43, 33.72, 130.20, 130.52)          # 福岡市のおおよその範囲（南・北・西・東）。正確な範囲は areas の境界で絞る
WIKI = "https://ja.wikipedia.org/w/api.php"
COMMONS = "https://commons.wikimedia.org/w/api.php"
NEAR_SPOT_M = 150
NEAR_SPOT_COMMONS_M = 100
SAME_NAME_M = 1000
MIN_FILES = 5

ADMIN_TITLE = re.compile(r"(都|道|府|県|市|区|町|村|郡|丁目|地方|地区|平野|半島)$")
# 題名の語 → 種別の案（上から順に最初に当たったもの。当たらなければ空欄＝人が選ぶ）
CATEGORY_HINTS = [
    (r"(駅|停留場|電停|信号場)$", "station"),
    (r"(神社|寺|宮|院|大社|稲荷|観音|天満宮)$", "shrine_temple"),
    (r"(小学校|中学校|高等学校|高校|大学|学院|学園|専門学校|特別支援学校)$", "school"),
    (r"(公園|庭園|緑地|広場)$", "park"),
    (r"(橋|大橋|歩道橋)$", "bridge_overpass"),
    (r"(遺跡|古墳|城|城跡|館|旧.+)$", "heritage"),
    (r"(図書館|郵便局|市役所|区役所|ホール|会館|博物館|美術館|体育館|センター|消防署|警察署|裁判所)$", "civic"),
    (r"(港|ふ頭|埠頭|ターミナル)$", "port"),
    (r"(山|丘|岳|峠)$", "hill"),
    (r"(島)$", "island"),
    (r"(ビル|タワー|モール|百貨店|ショッピング.*)$", "commercial_facility"),
]


def clean_title(title: str) -> str:
    """記事名から「 (糸島市)」のような補足を除く"""
    return re.sub(r"\s*[（(][^）)]*[）)]\s*$", "", title or "").strip()


def suggest_category(name: str) -> str | None:
    for pat, cat in CATEGORY_HINTS:
        if re.search(pat, name):
            return cat
    return None


def is_admin_title(name: str) -> bool:
    return bool(ADMIN_TITLE.search(name))


def get(url: str, params: dict, session=requests, sleep=time.sleep) -> dict:
    for attempt in range(3):
        sleep(INTERVAL_S)
        try:
            r = session.get(url, params=params, timeout=40, headers={"User-Agent": UA})
            if r.status_code == 200:
                return r.json() or {}
        except (requests.RequestException, ValueError):
            pass
        sleep(3 * (attempt + 1))
    return {}


def grid(step_m: float):
    dlat = step_m / 111000
    dlon = step_m / (111000 * math.cos(math.radians(33.58)))
    lat = BBOX[0]
    while lat <= BBOX[1]:
        lon = BBOX[2]
        while lon <= BBOX[3]:
            yield round(lat, 5), round(lon, 5)
            lon += dlon
        lat += dlat


def inside(lat: float, lon: float) -> bool:
    return BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lon <= BBOX[3]


def geosearch(url: str, step_m: int, radius_m: int, namespace: int | None, session=requests, sleep=time.sleep):
    """格子ごとに geosearch。返り値：({pageid: 点}, 上限500件に達した区画の数)"""
    seen, truncated = {}, 0
    for lat, lon in grid(step_m):
        params = {"action": "query", "format": "json", "formatversion": "2", "list": "geosearch",
                  "gscoord": f"{lat}|{lon}", "gsradius": radius_m, "gslimit": 500}
        if namespace is not None:
            params["gsnamespace"] = namespace
        res = (get(url, params, session, sleep).get("query") or {}).get("geosearch") or []
        truncated += len(res) >= 500
        for p in res:
            if inside(p["lat"], p["lon"]):
                seen[p["pageid"]] = {"pageid": p["pageid"], "title": p["title"], "lat": p["lat"], "lon": p["lon"]}
    return seen, truncated


NEAREST_SQL = """
select a.area_code,
       (select s.name from spots s where s.data_status <> 'archived'
         order by s.geom <-> ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326) limit 1) as near_name,
       (select ST_Distance(s.geom, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography) from spots s
         where s.data_status <> 'archived' order by s.geom <-> ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326) limit 1) as near_m,
       exists (select 1 from spots s where s.data_status <> 'archived'
                and ST_DWithin(s.geom, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography, %(same_m)s)
                and %(name)s <> '' and (s.name = %(name)s or similarity(s.name, %(name)s) > 0.5
                     or %(name)s = any(s.alt_names))) as same_name
  from (select area_at(%(lat)s, %(lon)s) as area_code) a
"""


def judge(conn, p: dict, name: str) -> dict:
    return conn.execute(NEAREST_SQL, {"lat": p["lat"], "lon": p["lon"], "name": name, "same_m": SAME_NAME_M}).fetchone()


def wikipedia_candidates(conn, points: dict, stats: dict) -> list[dict]:
    out = []
    for p in points.values():
        name = clean_title(p["title"])
        if not name or is_admin_title(name):
            stats["skipped_admin"] = stats.get("skipped_admin", 0) + 1
            continue
        j = judge(conn, p, name)
        if j["area_code"] is None:
            stats["skipped_outside"] = stats.get("skipped_outside", 0) + 1
        elif j["near_m"] is not None and j["near_m"] <= NEAR_SPOT_M:
            stats["skipped_near"] = stats.get("skipped_near", 0) + 1
        elif j["same_name"]:
            stats["skipped_same_name"] = stats.get("skipped_same_name", 0) + 1
        else:
            out.append({"source": "wikipedia", "source_key": f"wikipedia:{p['pageid']}", "name": name, "lat": p["lat"],
                        "lon": p["lon"], "area_code": j["area_code"], "suggest_cat": suggest_category(name),
                        "source_url": "https://ja.wikipedia.org/wiki/" + quote(p["title"].replace(" ", "_")),
                        "files": None, "samples": [], "near_name": j["near_name"], "near_m": j["near_m"]})
    return out


def commons_candidates(conn, points: dict, stats: dict) -> list[dict]:
    cells: dict[tuple, list] = {}
    for p in points.values():
        cells.setdefault((round(p["lat"], 3), round(p["lon"], 3)), []).append(p)
    out = []
    for (la, lo), ps in cells.items():
        if len(ps) < MIN_FILES:
            continue
        cx = {"lat": sum(p["lat"] for p in ps) / len(ps), "lon": sum(p["lon"] for p in ps) / len(ps)}
        j = judge(conn, cx, "")
        if j["area_code"] is None:
            stats["commons_skipped_outside"] = stats.get("commons_skipped_outside", 0) + 1
        elif j["near_m"] is not None and j["near_m"] <= NEAR_SPOT_COMMONS_M:
            stats["commons_skipped_near"] = stats.get("commons_skipped_near", 0) + 1
        else:
            samples = [{"title": p["title"].replace("File:", ""),
                        "url": "https://commons.wikimedia.org/wiki/" + quote(p["title"].replace(" ", "_"))} for p in ps[:5]]
            out.append({"source": "commons", "source_key": f"commons:{la:.3f},{lo:.3f}", "name": "（名称未設定）写真の多い場所",
                        "lat": round(cx["lat"], 6), "lon": round(cx["lon"], 6), "area_code": j["area_code"], "suggest_cat": None,
                        "source_url": f"https://commons.wikimedia.org/wiki/Special:Nearby#/coord/{cx['lat']:.5f},{cx['lon']:.5f}",
                        "files": len(ps), "samples": samples, "near_name": j["near_name"], "near_m": j["near_m"]})
    return out


INSERT_SQL = """
insert into spot_additions (source, source_key, name, lat, lon, area_code, suggest_cat, source_url, files, samples, near_name, near_m)
values (%(source)s, %(source_key)s, %(name)s, %(lat)s, %(lon)s, %(area_code)s, %(suggest_cat)s, %(source_url)s, %(files)s,
        %(samples)s::jsonb, %(near_name)s, %(near_m)s)
on conflict (source_key) do nothing
"""


def save(conn, rows: list[dict]) -> int:
    """保存（決めた候補は上書きしない）。新しく入った件数を返す"""
    before = conn.execute("select count(*) n from spot_additions").fetchone()["n"]
    with conn.cursor() as cur:
        cur.executemany(INSERT_SQL, [{**r, "samples": json.dumps(r["samples"], ensure_ascii=False)} for r in rows])
    conn.commit()
    return conn.execute("select count(*) n from spot_additions").fetchone()["n"] - before


def main(argv=None):
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default="additions-out")
    a = ap.parse_args(argv)
    with connect() as conn, job(conn, "addition_candidates", None) as stats:
        wpts, wtr = geosearch(WIKI, 6000, 5000, None)
        cpts, ctr = geosearch(COMMONS, 1600, 1000, 6)
        stats.update(wikipedia_points=len(wpts), commons_points=len(cpts), truncated_wikipedia=wtr, truncated_commons=ctr,
                     dry_run=a.dry_run)
        rows = wikipedia_candidates(conn, wpts, stats) + commons_candidates(conn, cpts, stats)
        stats["candidates"] = len(rows)
        if a.apply:
            stats["saved_new"] = save(conn, rows)
        out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
        with open(out / "additions.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["由来", "名前", "緯度", "経度", "区", "種別の案", "写真の枚数", "最寄りの地点", "距離m", "出典"])
            for r in rows:
                w.writerow([r["source"], r["name"], r["lat"], r["lon"], r["area_code"], r["suggest_cat"] or "",
                            r["files"] or "", r["near_name"] or "", round(r["near_m"] or 0), r["source_url"]])
        print(json.dumps({**stats}, ensure_ascii=False))


if __name__ == "__main__":
    main()
