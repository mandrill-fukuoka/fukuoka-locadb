"""raw_candidates を重複統合して spots に登録する

- 同名（類似度0.6以上）かつ 80m 以内、または同カテゴリで 30m 以内 → 同一地点として統合
- それ以外は新規地点（status=candidate, confidence=unverified）
"""
import argparse

from .db import connect, job

CATEGORY_LABEL_SQL = "select name from spot_categories where code=%s"


def next_code(conn, area_code: str) -> str:
    prefix = "FUK-" + area_code.split("-")[1][:3].upper()
    n = conn.execute("select count(*) as c from spots where area_code=%s", (area_code,)).fetchone()["c"]
    return f"{prefix}-S{n + 1:04d}"


def run(area_code: str) -> dict:
    with connect() as conn, job(conn, f"merge:{area_code}") as stats:
        rows = conn.execute(
            """select id, source, source_ref, name, category_hint,
                      ST_X(geom::geometry) lon, ST_Y(geom::geometry) lat
               from raw_candidates
               where spot_id is null and raw->>'area' = %s
               order by id""",
            (area_code,),
        ).fetchall()
        created = merged = 0
        for r in rows:
            match = conn.execute(
                """select id from spots
                   where status <> 'archived'
                     and ST_DWithin(geom, ST_SetSRID(ST_MakePoint(%(lon)s,%(lat)s),4326)::geography, 80)
                     and ( (%(name)s::text is not null and similarity(name, %(name)s::text) >= 0.6)
                           or (category = %(cat)s::text and ST_DWithin(geom,
                               ST_SetSRID(ST_MakePoint(%(lon)s,%(lat)s),4326)::geography, 30)) )
                   order by geom <-> ST_SetSRID(ST_MakePoint(%(lon)s,%(lat)s),4326)::geography
                   limit 1""",
                {"lon": r["lon"], "lat": r["lat"], "name": r["name"], "cat": r["category_hint"]},
            ).fetchone()
            if match:
                spot_id = match["id"]
                merged += 1
            else:
                name = r["name"]
                if not name:
                    label = conn.execute(CATEGORY_LABEL_SQL, (r["category_hint"],)).fetchone()["name"]
                    name = f"（無名）{label} {r['lat']:.4f},{r['lon']:.4f}"
                spot_id = conn.execute(
                    """insert into spots (code, name, area_code, geom, category)
                       values (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, %s)
                       returning id""",
                    (next_code(conn, area_code), name, area_code, r["lon"], r["lat"], r["category_hint"]),
                ).fetchone()["id"]
                created += 1
            conn.execute("update raw_candidates set spot_id=%s where id=%s", (spot_id, r["id"]))
            conn.execute(
                """insert into sources (spot_id, kind, title, url)
                   values (%s, %s, %s, %s)""",
                (spot_id, r["source"], r["source_ref"],
                 "https://www.openstreetmap.org/" + r["source_ref"].split(":", 1)[1].split("@")[0]
                 if r["source"] == "osm" else None),
            )
        stats.update(created=created, merged=merged)
        print(f"  新規 {created} / 統合 {merged}")
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="fuk-chuo")
    run(ap.parse_args().area)
