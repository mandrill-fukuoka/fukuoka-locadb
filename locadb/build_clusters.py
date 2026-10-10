"""地点からクラスター（流れ）を自動生成して clusters / cluster_spots に登録する

人が保存した流れ（origin=search/human、status=reviewed/verified）は消さない。
対象外の地点は使わない。途中の地点は市全体から選ぶ（区の境をまたいでよい）。起点の区を流れの区とする。
移動時間は直線距離×1.3からの目安（実際の道のりの所要時間ではない）。
"""
import argparse

from .clustering import Spot, generate
from .db import connect, job
from .osm_source import AREAS


def load_spots(conn) -> list[tuple[Spot, str]]:
    rows = conn.execute(
        """select s.id, s.name, ST_Y(s.geom::geometry) lat, ST_X(s.geom::geometry) lon, s.area_code,
                  sc.landscape_group grp, lg.name grp_name, s.era_feel
           from spots s
           join spot_categories sc on sc.code = s.category
           join landscape_groups lg on lg.code = sc.landscape_group
           where s.data_status <> 'archived'
             and (s.name not like '（無名）%%'
                  or sc.landscape_group in ('port_industrial','vacant','river','sea','beach','lake','old_town'))""",
    ).fetchall()
    return [(Spot(r["id"], r["name"], r["lat"], r["lon"], r["grp"], r["grp_name"], r["era_feel"]), r["area_code"])
            for r in rows]


def run_all(run_id: int | None = None, top: int = 50) -> dict:
    with connect() as conn, job(conn, "clusters", run_id) as stats:
        loaded = load_spots(conn)
        spots = [s for s, _ in loaded]
        conn.execute("delete from clusters where origin='auto' and status='candidate'")
        n = 0
        for area in AREAS:
            anchors = [s for s, a in loaded if a == area]
            prefix = "FUK-" + area.split("-")[1][:3].upper()
            for mode in ("walk", "car"):
                for c in generate(spots, mode=mode, top=top, anchors=anchors):
                    n += 1
                    cid = conn.execute(
                        """insert into clusters (code, name, area_code, transition_score, transition_label,
                                                 total_minutes, travel_mode)
                           values (%s,%s,%s,%s,%s,%s,%s) returning id""",
                        (f"{prefix}-C{mode[0].upper()}{n:03d}", f"{c.spots[0].name} 〜 {c.spots[-1].name}", area,
                         c.score, c.label, c.minutes, mode)).fetchone()["id"]
                    for i, s in enumerate(c.spots):
                        conn.execute(
                            """insert into cluster_spots (cluster_id, seq, spot_id, minutes_from_prev, meters_from_prev)
                               values (%s,%s,%s,%s,%s)""",
                            (cid, i, s.id, c.legs_min[i - 1] if i else None, round(c.legs_m[i - 1]) if i else None))
        stats.update(spots=len(spots), clusters=n)
        print(f"    地点 {len(spots)} → 自動生成の流れ {n}")
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=50)
    run_all(top=ap.parse_args().top)
