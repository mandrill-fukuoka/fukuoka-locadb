"""地点の住所（町丁目まで）を国土地理院の逆ジオコーダーで入れる（無料・キー不要）

対象：住所が空の地点と、住所を入れた後に位置が変わった地点。
名前のない地点は「（無名）種別（町丁目）」と表示できるようにする。
"""
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from .db import connect, job

URL = "https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress"
MUNI = {"40131": "福岡市東区", "40132": "福岡市博多区", "40133": "福岡市中央区", "40134": "福岡市南区",
        "40135": "福岡市西区", "40136": "福岡市城南区", "40137": "福岡市早良区"}


def lookup(lon: float, lat: float, session=requests) -> str | None:
    for attempt in range(3):
        try:
            r = session.get(URL, params={"lat": f"{lat:.6f}", "lon": f"{lon:.6f}"}, timeout=20,
                            headers={"User-Agent": "fukuoka-locadb/2.0 (location research)"})
            if r.status_code == 200:
                res = (r.json() or {}).get("results") or {}
                muni = MUNI.get(res.get("muniCd", ""), "")
                town = (res.get("lv01Nm") or "").replace("－", "")
                return (muni + town) or None
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2 * (attempt + 1))
    return None


# 住所が引けない（海・砂浜など）名前のない地点は、近くの目印の名前で「（無名）種別（○○付近）」にする（2026-10-21）
LANDMARK_CATS = ["station", "park", "shrine_temple", "school", "beach", "fishing_port", "port", "island", "hill",
                 "heritage", "civic", "bridge_overpass"]
LANDMARK_RADIUS_M = 1500


def landmark_name(conn, spot_id: int) -> str | None:
    r = conn.execute(
        """select n.name from spots s
             join spots n on n.id <> s.id and n.data_status <> 'archived' and n.name not like '（無名）%%'
                         and n.category = any(%s) and ST_DWithin(n.geom, s.geom, %s)
            where s.id = %s order by ST_Distance(n.geom, s.geom), n.id limit 1""",
        (LANDMARK_CATS, LANDMARK_RADIUS_M, spot_id)).fetchone()
    return r["name"] if r else None


def run(run_id: int | None = None, limit: int = 20000) -> dict:
    with connect() as conn, job(conn, "address", run_id) as stats:
        rows = conn.execute(
            """select s.id, s.name, s.category, sc.name catname, ST_X(s.geom::geometry) lon, ST_Y(s.geom::geometry) lat
                 from spots s join spot_categories sc on sc.code = s.category
                where s.data_status <> 'archived'
                  and (s.address is null or s.address_at < (select max(h.at) from spot_history h
                                                             where h.spot_id = s.id and h.event = 'moved'))
                order by s.id limit %s""", (limit,)).fetchall()
        ok = ng = 0
        batch = []
        no_addr = []                       # 住所が引けなかった名前のない地点（目印の名前を付ける）

        def flush():
            if batch:
                with conn.cursor() as cur:
                    cur.executemany("""update spots set address=%s, address_at=now(),
                                         name = case when %s::text is not null then %s else name end where id=%s""", batch)
                conn.commit()
                batch.clear()
        # 国土地理院のサービスへの負荷を抑えつつ、4件ずつ並行して問い合わせる
        with requests.Session() as sess, ThreadPoolExecutor(max_workers=4) as pool:
            for r, addr in zip(rows, pool.map(lambda r: lookup(r["lon"], r["lat"], sess), rows)):
                if not addr:
                    ng += 1
                    if r["name"] == f"（無名）{r['catname']}":
                        no_addr.append(r)
                    continue
                town = addr.split("区", 1)[-1] if "区" in addr else addr
                new_name = None
                if r["name"].startswith("（無名）") and town:
                    new_name = f"（無名）{r['catname']}（{town}）"
                batch.append((addr, new_name, new_name, r["id"]))
                ok += 1
                if len(batch) >= 200:
                    flush(); print(f"    住所 {ok}/{len(rows)}", flush=True)
        flush()
        named = 0
        for r in no_addr:
            mark = landmark_name(conn, r["id"])
            if mark:
                conn.execute("update spots set name = %s where id = %s", (f"（無名）{r['catname']}（{mark}付近）", r["id"]))
                named += 1
        conn.commit()
        stats.update(target=len(rows), filled=ok, failed=ng, landmark_named=named)
        print(f"    住所を入れた地点 {ok}（取得できず {ng}）")
    return stats
