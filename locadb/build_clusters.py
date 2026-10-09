"""地点からクラスターを自動生成して clusters / cluster_spots に登録する

人が確認済み（reviewed/verified）または手動作成（origin=human）のクラスターは消さない。
"""
import argparse

from .clustering import Spot, generate
from .db import connect, job


def load_spots(conn, area_code: str) -> list[Spot]:
    rows = conn.execute(
        """select s.id, s.name, ST_Y(s.geom::geometry) lat, ST_X(s.geom::geometry) lon,
                  sc.landscape_group grp, lg.name grp_name, s.era_feel
           from spots s
           join spot_categories sc on sc.code = s.category
           join landscape_groups lg on lg.code = sc.landscape_group
           where s.area_code=%s and s.status <> 'archived'
             and (s.name not like '（無名）%%'
                  or sc.landscape_group in ('port_industrial','vacant','river','sea','beach','lake'))""",
        (area_code,),
    ).fetchall()
    return [Spot(r["id"], r["name"], r["lat"], r["lon"], r["grp"], r["grp_name"], r["era_feel"]) for r in rows]


def run(area_code: str, top: int = 50) -> dict:
    with connect() as conn, job(conn, f"clusters:{area_code}") as stats:
        spots = load_spots(conn, area_code)
        conn.execute(
            "delete from clusters where area_code=%s and origin='auto' and status='candidate'", (area_code,))
        prefix = "FUK-" + area_code.split("-")[1][:3].upper()
        n = 0
        for mode in ("walk", "car"):
            for c in generate(spots, mode=mode, top=top):
                n += 1
                cid = conn.execute(
                    """insert into clusters (code, name, area_code, transition_score, transition_label,
                                             total_minutes, travel_mode)
                       values (%s,%s,%s,%s,%s,%s,%s) returning id""",
                    (f"{prefix}-C{mode[0].upper()}{n:03d}",
                     f"{c.spots[0].name} 〜 {c.spots[-1].name}", area_code,
                     c.score, c.label, c.minutes, mode),
                ).fetchone()["id"]
                for i, s in enumerate(c.spots):
                    conn.execute(
                        """insert into cluster_spots (cluster_id, seq, spot_id, minutes_from_prev, meters_from_prev)
                           values (%s,%s,%s,%s,%s)""",
                        (cid, i, s.id, c.legs_min[i - 1] if i else None,
                         round(c.legs_m[i - 1]) if i else None),
                    )
        stats.update(spots=len(spots), clusters=n)
        print(f"  地点 {len(spots)} → クラスター {n}")
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="fuk-chuo")
    ap.add_argument("--top", type=int, default=50)
    a = ap.parse_args()
    run(a.area, a.top)
