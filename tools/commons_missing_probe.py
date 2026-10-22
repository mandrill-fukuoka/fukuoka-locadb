"""DBにない「名前のある場所」が、Wikipedia(ja)・Wikimedia Commons にどれだけあるかを数える（読み取りだけ。DBへは SELECT のみ）

  python tools/commons_missing_probe.py [--out DIR]

やること
  A. 日本語Wikipediaの座標つき記事（福岡市の範囲を格子で走査）のうち、近く（150m以内）に地点がないものを出す
  B. Commons の座標つき写真（格子で走査）のうち、近く（100m以内）に地点がないものの数と密集地を出す
決まり：外部への取得は直列・1秒以上の間隔、User-Agent に連絡先。AI・検索エンジンの画像は使わない。DBは変更しない。
"""
import argparse
import csv
import json
import math
import time
from pathlib import Path

import requests

from locadb.db import connect

UA = "fukuoka-locadb/2.0 (https://github.com/mandrillfuk-dev/fukuoka-locadb; location research)"
INTERVAL_S = 1.1
BBOX = (33.43, 33.72, 130.20, 130.52)          # 福岡市のおおよその範囲（南・北・西・東）
WIKI = "https://ja.wikipedia.org/w/api.php"
COMMONS = "https://commons.wikimedia.org/w/api.php"


def get(url, params):
    for attempt in range(3):
        time.sleep(INTERVAL_S)
        try:
            r = requests.get(url, params=params, timeout=40, headers={"User-Agent": UA})
            if r.status_code == 200:
                return r.json()
        except (requests.RequestException, ValueError):
            pass
        time.sleep(3 * (attempt + 1))
    return {}


def grid(step_m):
    dlat = step_m / 111000
    dlon = step_m / (111000 * math.cos(math.radians(33.58)))
    lat = BBOX[0]
    while lat <= BBOX[1]:
        lon = BBOX[2]
        while lon <= BBOX[3]:
            yield round(lat, 5), round(lon, 5)
            lon += dlon
        lat += dlat


def inside(lat, lon):
    return BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lon <= BBOX[3]


def wiki_points():
    seen, trunc = {}, 0
    for lat, lon in grid(6000):
        d = get(WIKI, {"action": "query", "format": "json", "formatversion": "2", "list": "geosearch",
                       "gscoord": f"{lat}|{lon}", "gsradius": 5000, "gslimit": 500})
        res = (d.get("query") or {}).get("geosearch") or []
        trunc += len(res) >= 500
        for p in res:
            if inside(p["lat"], p["lon"]):
                seen[p["pageid"]] = {"title": p["title"], "lat": p["lat"], "lon": p["lon"]}
    return list(seen.values()), trunc


def commons_points():
    seen, trunc = {}, 0
    for lat, lon in grid(1600):
        d = get(COMMONS, {"action": "query", "format": "json", "formatversion": "2", "list": "geosearch",
                          "gscoord": f"{lat}|{lon}", "gsradius": 1000, "gsnamespace": 6, "gslimit": 500})
        res = (d.get("query") or {}).get("geosearch") or []
        trunc += len(res) >= 500
        for p in res:
            if inside(p["lat"], p["lon"]):
                seen[p["pageid"]] = {"title": p["title"], "lat": p["lat"], "lon": p["lon"]}
    return list(seen.values()), trunc


def nearest(conn, pts, key):
    """各点の最寄りの地点（名前・距離m）。公開対象外（archived）は数えない"""
    out = []
    with conn.cursor() as cur:
        for p in pts:
            cur.execute("""select name, st_distance(geom::geography, st_setsrid(st_makepoint(%s,%s),4326)::geography) d
                           from spots where data_status <> 'archived'
                           order by geom <-> st_setsrid(st_makepoint(%s,%s),4326) limit 1""",
                        (p["lon"], p["lat"], p["lon"], p["lat"]))
            r = cur.fetchone()
            out.append({**p, "near_name": r["name"] if r else None, "near_m": round(r["d"]) if r else None})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="probe-out")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    wp, wt = wiki_points()
    cp, ct = commons_points()
    with connect() as conn:
        wn = nearest(conn, wp, "w")
        cn = nearest(conn, cp, "c")
    miss_w = [p for p in wn if p["near_m"] is None or p["near_m"] > 150]
    miss_c = [p for p in cn if p["near_m"] is None or p["near_m"] > 100]
    with open(out / "wikipedia_missing.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["title", "lat", "lon", "near_name", "near_m"]); w.writeheader()
        w.writerows(sorted(miss_w, key=lambda p: -(p["near_m"] or 99999)))
    cells = {}
    for p in miss_c:                                         # 100m四方に丸めて、写真が多い所を出す
        k = (round(p["lat"], 3), round(p["lon"], 3))
        cells.setdefault(k, []).append(p["title"])
    top = sorted(cells.items(), key=lambda kv: -len(kv[1]))[:40]
    with open(out / "commons_dense_missing.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["lat", "lon", "files", "sample_titles"])
        for (la, lo), ts in top:
            w.writerow([la, lo, len(ts), " / ".join(t.replace("File:", "") for t in ts[:3])])
    summary = (f"- Wikipedia(ja) 座標つき記事：{len(wp)} 件（走査が上限500件に達した区画：{wt}）／近く150m以内に地点なし：{len(miss_w)} 件\n"
               f"- Commons 座標つき写真：{len(cp)} 件（上限500件に達した区画：{ct}）／近く100m以内に地点なし：{len(miss_c)} 件"
               f"（そのうち写真が5枚以上集まる100m四方：{sum(1 for v in cells.values() if len(v) >= 5)} か所）\n"
               "\n### Wikipedia：地点がない記事の例（遠い順 先頭30）\n"
               + "\n".join(f"- {p['title']}（最寄り {p['near_name']} {p['near_m']}m）" for p in
                           sorted(miss_w, key=lambda p: -(p['near_m'] or 99999))[:30]))
    (out / "summary.md").write_text(summary, encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    main()
