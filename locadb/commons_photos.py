"""写真候補の収集：Wikimedia Commons の位置情報つき写真を、地点ごとに候補として集める（AIなし・費用ゼロ）

  python -m locadb.commons_photos --dry-run [--limit 200] [--out DIR]    読み取りのみ。候補の一覧を出すだけ
  python -m locadb.commons_photos --apply   [--limit 200]                候補を spot_photo_candidates に保存する（公開はしない）

決まり
  - 取るのは CC0・パブリックドメイン・CC BY・CC BY-SA だけ。NC・ND・fair use・ライセンス不明は取らない
  - 作者・ライセンス名・出典ページがないものは取らない（Commons の再利用の決まり：作者表示・ライセンスへのリンク）
  - 外部への取得は1秒以上の間隔で直列。User-Agent に連絡先（リポジトリ）を入れる。検索エンジンの画像は取らない
  - 候補は人が確認画面で採用してはじめて公開される（photo_decide）。画像は保存せず、縮小画像のURLを参照する
"""
import argparse
import csv
import html
import json
import math
import re
import time
from pathlib import Path

import requests

from .db import connect, job

API = "https://commons.wikimedia.org/w/api.php"
UA = "fukuoka-locadb/2.0 (https://github.com/mandrillfuk-dev/fukuoka-locadb; location research)"
INTERVAL_S = 1.1
RADIUS_M = 100
PER_SPOT = 3
THUMB_W = 640
# 写真を探す種別（景観として撮影の需要があり、位置が建物・場所にひもづくもの）
PHOTO_CATS = ["shrine_temple", "heritage", "park", "station", "bridge_overpass", "school", "civic", "hill",
              "island", "beach", "fishing_port", "port", "pond", "commercial_facility", "new_building"]

LICENSE_OK = re.compile(r"^(CC0|Public domain|PD[\s-]|CC BY(?:-SA)?[\s-]\d)", re.I)
LICENSE_NG = re.compile(r"\b(NC|ND)\b|fair use|non-?free|copyrighted", re.I)


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def license_ok(name: str, nonfree: str | None = None) -> bool:
    return bool(name) and bool(LICENSE_OK.search(name)) and not LICENSE_NG.search(name) and (nonfree or "").lower() not in ("true", "1")


def dist_m(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742000 * math.asin(math.sqrt(a))


def norm(s: str) -> str:
    return re.sub(r"[\s・\-_.()（）]+", "", (s or "").lower())


def fetch(lat: float, lon: float, session=requests) -> dict:
    """geosearch の結果に画像情報をつけて1回で取る"""
    params = {
        "action": "query", "format": "json", "formatversion": "2", "generator": "geosearch",
        "ggscoord": f"{lat:.6f}|{lon:.6f}", "ggsradius": RADIUS_M, "ggsnamespace": 6, "ggslimit": 10,
        "prop": "imageinfo|coordinates", "iiprop": "url|extmetadata", "iiurlwidth": THUMB_W,
        "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl|ImageDescription|DateTimeOriginal|NonFree",
        "colimit": 10,
    }
    for attempt in range(3):
        try:
            r = session.get(API, params=params, timeout=30, headers={"User-Agent": UA})
            if r.status_code == 200:
                return r.json() or {}
        except (requests.RequestException, ValueError):
            pass
        time.sleep(3 * (attempt + 1))
    return {}


def candidates_of(spot: dict, data: dict) -> list[dict]:
    """1地点ぶんの応答から、条件に合う候補（最大 PER_SPOT 件）を選ぶ"""
    out = []
    name = norm(spot["name"])
    for pg in (data.get("query") or {}).get("pages", []):
        info = (pg.get("imageinfo") or [None])[0]
        if not info or not info.get("thumburl") or not info.get("descriptionurl"):
            continue
        meta = {k: (v or {}).get("value") for k, v in (info.get("extmetadata") or {}).items()}
        lic = clean(meta.get("LicenseShortName"))
        if not license_ok(lic, meta.get("NonFree")):
            continue
        author = clean(meta.get("Artist"))
        if not author:                                          # 作者が分からないものは表示できない
            continue
        co = (pg.get("coordinates") or [None])[0]
        d = dist_m(spot["lat"], spot["lon"], co["lat"], co["lon"]) if co else None
        title = pg.get("title") or ""
        desc = clean(meta.get("ImageDescription"))
        out.append({
            "spot_id": spot["id"], "file_title": title, "page_url": info["descriptionurl"], "image_url": info["thumburl"],
            "author": author[:200], "license": lic, "license_url": meta.get("LicenseUrl"),
            "description": desc[:300], "shot_at": clean(meta.get("DateTimeOriginal"))[:40] or None,
            "dist_m": d, "name_match": bool(name) and len(name) >= 2 and (name in norm(title) or name in norm(desc)),
        })
    out.sort(key=lambda c: (not c["name_match"], c["dist_m"] if c["dist_m"] is not None else 1e9))
    return out[:PER_SPOT]


SPOTS_SQL = """
select s.id, s.code, s.name, ST_Y(s.geom::geometry) lat, ST_X(s.geom::geometry) lon
  from spots s
 where s.data_status <> 'archived' and s.name not like '（無名）%%' and s.category = any(%s)
   and not exists (select 1 from spot_photo_search q where q.spot_id = s.id)
   and not exists (select 1 from spot_photos p where p.spot_id = s.id)
 order by s.id limit %s
"""
INSERT_SQL = """
insert into spot_photo_candidates (spot_id, file_title, page_url, image_url, author, license, license_url,
                                   description, shot_at, dist_m, name_match)
values (%(spot_id)s, %(file_title)s, %(page_url)s, %(image_url)s, %(author)s, %(license)s, %(license_url)s,
        %(description)s, %(shot_at)s, %(dist_m)s, %(name_match)s)
on conflict (spot_id, file_title) do nothing
"""
MARK_SQL = """insert into spot_photo_search (spot_id, found) values (%s, %s)
                on conflict (spot_id) do update set searched_at = now(), found = excluded.found"""


def collect(conn, limit: int, session=requests, sleep=time.sleep) -> tuple[list[dict], dict, list[tuple]]:
    spots = conn.execute(SPOTS_SQL, (PHOTO_CATS, limit)).fetchall()
    found, marks, stats = [], [], {"spots": len(spots), "with_candidates": 0, "candidates": 0}
    for i, sp in enumerate(spots):
        cs = candidates_of(sp, fetch(sp["lat"], sp["lon"], session))
        marks.append((sp["id"], len(cs)))
        if cs:
            stats["with_candidates"] += 1
            stats["candidates"] += len(cs)
            for c in cs:
                found.append({**c, "spot_code": sp["code"], "spot_name": sp["name"]})
        if i + 1 < len(spots):
            sleep(INTERVAL_S)
    return found, stats, marks


def main(argv=None):
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--out", default="commons-out")
    a = ap.parse_args(argv)
    with connect() as conn, job(conn, "commons_photos", None) as stats:
        found, st, marks = collect(conn, a.limit)
        stats.update(st, dry_run=a.dry_run)
        if a.apply:
            with conn.cursor() as cur:
                cur.executemany(INSERT_SQL, found)
                cur.executemany(MARK_SQL, marks)
            conn.commit()
        out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
        with open(out / "candidates.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["地点コード", "地点名", "ファイル", "距離m", "名前一致", "ライセンス", "作者", "出典ページ"])
            for c in found:
                w.writerow([c["spot_code"], c["spot_name"], c["file_title"], round(c["dist_m"] or 0), "○" if c["name_match"] else "",
                            c["license"], c["author"], c["page_url"]])
        print(json.dumps({**st, "dry_run": a.dry_run}, ensure_ascii=False))


if __name__ == "__main__":
    main()
