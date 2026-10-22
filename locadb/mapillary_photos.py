"""写真候補の収集（その2）：Mapillary の街路写真を、地点ごとに候補として集める（AIなし・費用ゼロ）

  python -m locadb.mapillary_photos --dry-run [--limit 200] [--out DIR]    読み取りのみ
  python -m locadb.mapillary_photos --apply   [--limit 200]                候補を spot_photo_candidates（source='mapillary'）に保存

決まり
  - アクセストークンは環境変数 MAPILLARY_TOKEN（GitHub Secrets）だけから読む。表示・保存・ログ出力はしない
  - 画像は CC BY-SA 4.0。作者（creator.username）がないものは取らない。表示のとき作者・ライセンス・Mapillary の画像ページへのリンクを出す
  - 外部への取得は1秒以上の間隔で直列。画像そのものは保存しない（縮小画像のURLを参照）。顔・ナンバーのぼかしは Mapillary 側のまま使う
  - 候補は人が確認画面で採用してはじめて公開される（photo_decide）
"""
import argparse
import csv
import json
import math
import os
import time
from pathlib import Path

import requests

from .commons_photos import PHOTO_CATS, dist_m
from .db import connect, job

API = "https://graph.mapillary.com/images"
UA = "fukuoka-locadb/2.0 (https://github.com/mandrillfuk-dev/fukuoka-locadb; location research)"
INTERVAL_S = 1.1
RADIUS_M = 50
PER_SPOT = 2
LICENSE = "CC BY-SA 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
FIELDS = "id,captured_at,compass_angle,geometry,creator,thumb_1024_url,is_pano"


def bbox(lat: float, lon: float, r: float = RADIUS_M) -> str:
    dlat = r / 111320
    dlon = r / (111320 * math.cos(math.radians(lat)))
    return f"{lon - dlon:.6f},{lat - dlat:.6f},{lon + dlon:.6f},{lat + dlat:.6f}"


def fetch(lat: float, lon: float, token: str, session=requests) -> dict:
    params = {"access_token": token, "bbox": bbox(lat, lon), "fields": FIELDS, "limit": 50}
    for attempt in range(3):
        try:
            r = session.get(API, params=params, timeout=30, headers={"User-Agent": UA})
            if r.status_code == 200:
                return r.json() or {}
            if r.status_code in (400, 401, 403):               # トークン・条件の誤り：続けても同じなので止める
                raise PermissionError(f"Mapillary API {r.status_code}")
        except PermissionError:
            raise
        except (requests.RequestException, ValueError):
            pass
        time.sleep(3 * (attempt + 1))
    return {}


def angle_gap(a, b) -> float:
    d = abs((a or 0) - (b or 0)) % 360
    return min(d, 360 - d)


def candidates_of(spot: dict, data: dict) -> list[dict]:
    """近い順に、向き（compass）が違うものを PER_SPOT 件まで選ぶ。パノラマ・作者なし・画像URLなしは除く"""
    rows = []
    for im in data.get("data") or []:
        user = ((im.get("creator") or {}).get("username") or "").strip()
        url = im.get("thumb_1024_url")
        co = ((im.get("geometry") or {}).get("coordinates")) or None
        if not im.get("id") or not user or not url or not co or im.get("is_pano"):
            continue
        rows.append({"im": im, "user": user, "url": url, "d": dist_m(spot["lat"], spot["lon"], co[1], co[0])})
    rows.sort(key=lambda r: (r["d"], -(r["im"].get("captured_at") or 0)))
    out, angles = [], []
    for r in rows:
        ang = r["im"].get("compass_angle")
        if any(angle_gap(ang, a) < 60 for a in angles):
            continue
        angles.append(ang)
        ts = r["im"].get("captured_at")
        out.append({
            "spot_id": spot["id"], "file_title": f"mapillary:{r['im']['id']}",
            "page_url": f"https://www.mapillary.com/app/?pKey={r['im']['id']}&focus=photo",
            "image_url": r["url"], "author": r["user"][:200], "license": LICENSE, "license_url": LICENSE_URL,
            "description": "Mapillary の街路写真", "shot_at": time.strftime("%Y-%m-%d", time.gmtime(ts / 1000)) if ts else None,
            "dist_m": r["d"], "name_match": False,
        })
        if len(out) >= PER_SPOT:
            break
    return out


SPOTS_SQL = """
select s.id, s.code, s.name, ST_Y(s.geom::geometry) lat, ST_X(s.geom::geometry) lon
  from spots s
 where s.data_status <> 'archived' and s.name not like '（無名）%%' and s.category = any(%s)
   and not exists (select 1 from spot_photo_search_mly q where q.spot_id = s.id)
   and not exists (select 1 from spot_photos p where p.spot_id = s.id)
 order by s.id limit %s
"""
INSERT_SQL = """
insert into spot_photo_candidates (spot_id, source, file_title, page_url, image_url, author, license, license_url,
                                   description, shot_at, dist_m, name_match)
values (%(spot_id)s, 'mapillary', %(file_title)s, %(page_url)s, %(image_url)s, %(author)s, %(license)s, %(license_url)s,
        %(description)s, %(shot_at)s, %(dist_m)s, %(name_match)s)
on conflict (spot_id, file_title) do nothing
"""
MARK_SQL = """insert into spot_photo_search_mly (spot_id, found) values (%s, %s)
                on conflict (spot_id) do update set searched_at = now(), found = excluded.found"""


def collect(conn, limit: int, token: str, session=requests, sleep=time.sleep):
    spots = conn.execute(SPOTS_SQL, (PHOTO_CATS, limit)).fetchall()
    found, marks, stats = [], [], {"spots": len(spots), "with_candidates": 0, "candidates": 0}
    for i, sp in enumerate(spots):
        cs = candidates_of(sp, fetch(sp["lat"], sp["lon"], token, session))
        marks.append((sp["id"], len(cs)))
        if cs:
            stats["with_candidates"] += 1
            stats["candidates"] += len(cs)
            found += [{**c, "spot_code": sp["code"], "spot_name": sp["name"]} for c in cs]
        if i + 1 < len(spots):
            sleep(INTERVAL_S)
    return found, stats, marks


def main(argv=None):
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--out", default="mapillary-out")
    a = ap.parse_args(argv)
    token = os.environ.get("MAPILLARY_TOKEN", "")
    if not token:
        raise SystemExit("MAPILLARY_TOKEN が未設定です（GitHub Secrets に登録してください）")
    with connect() as conn, job(conn, "mapillary_photos", None) as stats:
        found, st, marks = collect(conn, a.limit, token)
        stats.update(st, dry_run=a.dry_run)
        if a.apply:
            with conn.cursor() as cur:
                cur.executemany(INSERT_SQL, found)
                cur.executemany(MARK_SQL, marks)
            conn.commit()
        out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
        with open(out / "candidates.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["地点コード", "地点名", "画像", "距離m", "作者", "撮影日", "出典ページ"])
            for c in found:
                w.writerow([c["spot_code"], c["spot_name"], c["file_title"], round(c["dist_m"] or 0), c["author"], c["shot_at"], c["page_url"]])
        print(json.dumps({**st, "dry_run": a.dry_run}, ensure_ascii=False))


if __name__ == "__main__":
    main()
