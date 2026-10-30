"""利用者のリストの場所を、OpenStreetMap の要素から位置を決めて手入力の地点として登録する（2026-10-30）

  python -m tools.curated_import --data data/curated/2026-10-30_list.json --dry-run   位置と近くの地点を出すだけ
  python -m tools.curated_import --data data/curated/2026-10-30_list.json --apply     create_manual_spot で登録する

位置の決め方（place）
  node             ノードの位置
  inside           面（閉じた way）の中の代表点
  middle           線の長さの半分の位置
  nearest_to_spot  名前が ways_named の道のうち、既存の地点 spot にいちばん近い位置
決まり：位置は推測しない。同じ名前の地点が200m以内にあれば登録しない（何度実行しても同じ）。出典として OSM の要素のURLを残す。
"""
import argparse
import json
from pathlib import Path

import osmium
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points

from locadb import osm_source
from locadb.db import connect

SAME_NAME_M = 200


class H(osmium.SimpleHandler):
    def __init__(self, want_ids: set, want_names: set):
        super().__init__()
        self.want_ids, self.want_names = want_ids, want_names
        self.geom = {}                      # "n123"/"w123" -> shapely（経度・緯度）
        self.named = {}                     # 名前 -> [LineString]

    def node(self, n):
        k = f"n{n.id}"
        if k in self.want_ids:
            self.geom[k] = Point(n.location.lon, n.location.lat)

    def way(self, w):
        k = f"w{w.id}"
        nm = w.tags.get("name:ja") or w.tags.get("name")
        if k not in self.want_ids and nm not in self.want_names:
            return
        try:
            cs = [(nd.location.lon, nd.location.lat) for nd in w.nodes]
        except osmium.InvalidLocationError:
            return
        if len(cs) < 2:
            return
        g = Polygon(cs) if len(cs) >= 4 and cs[0] == cs[-1] else LineString(cs)
        if k in self.want_ids:
            self.geom[k] = g
        if nm in self.want_names:
            self.named.setdefault(nm, []).append(LineString(cs))


def resolve(spec, h, conn):
    place = spec["place"]
    if place == "nearest_to_spot":
        row = conn.execute("""select ST_X(geom::geometry) lon, ST_Y(geom::geometry) lat from spots
                               where name = %s and data_status <> 'archived' order by id limit 1""", (spec["spot"],)).fetchone()
        lines = h.named.get(spec["ways_named"], [])
        if not row or not lines:
            return None
        p = Point(row["lon"], row["lat"])
        best = min(lines, key=lambda l: l.distance(p))
        return nearest_points(best, p)[0]
    g = h.geom.get(spec["osm"])
    if g is None:
        return None
    if place == "node":
        return g
    if place == "inside":
        return g.representative_point()
    if place == "middle":
        line = g.exterior if isinstance(g, Polygon) else g
        return line.interpolate(0.5, normalized=True)
    raise ValueError(place)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default="curated-out")
    a = ap.parse_args()
    data = json.loads(Path(a.data).read_text(encoding="utf-8"))
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    full, clipped = out / "kyushu.osm.pbf", out / "fukuoka.osm.pbf"
    osm_source.download(full); osm_source.clip(full, clipped); full.unlink()
    h = H({s["osm"] for s in data["spots"] if s.get("osm")}, {s["ways_named"] for s in data["spots"] if s.get("ways_named")})
    h.apply_file(str(clipped), locations=True, idx="flex_mem"); clipped.unlink()

    lines = ["| No | 名前 | 種別 | 位置 | 区 | 近い地点（m） | 結果 |", "|---|---|---|---|---|---|---|"]
    with connect() as conn:
        for s in data["spots"]:
            p = resolve(s, h, conn)
            if p is None:
                lines.append(f"| {s['no']} | {s['name']} | {s['category']} | - | - | - | ！OSMの要素が見つからない |"); continue
            lat, lon = round(p.y, 7), round(p.x, 7)
            area = conn.execute("select area_at(%s,%s) a", (lat, lon)).fetchone()["a"]
            near = conn.execute("""select s.name, round(ST_Distance(s.geom, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography)) d
                                     from spots s where s.data_status <> 'archived'
                                    order by s.geom <-> ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography limit 1""",
                                (lon, lat, lon, lat)).fetchone()
            same = conn.execute("""select id from spots where data_status <> 'archived' and (name = %s or name = any(%s))
                                     and ST_DWithin(geom, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, %s)""",
                                (s["name"], s.get("alt", []), lon, lat, SAME_NAME_M)).fetchone()
            where = f"{lat:.5f}, {lon:.5f}"
            nearby = f"{near['name']}（{int(near['d'])}）" if near else ""
            if not area:
                res = "！福岡市7区の外"
            elif same:
                res = f"登録済み（id={same['id']}）"
            elif a.apply:
                src = f"https://www.openstreetmap.org/{ {'n': 'node', 'w': 'way'}[s['osm'][0]] }/{s['osm'][1:]}" if s.get("osm") \
                    else f"https://www.openstreetmap.org/#map=18/{lat}/{lon}"
                r = conn.execute("select create_manual_spot(%s,%s,%s,%s,'{}',null,'unverified',%s,%s,'official',null,%s) r",
                                 (s["name"], lat, lon, s["category"], src,
                                  "OpenStreetMap の要素から位置を決めた（利用者の地点リスト 2026-10-30）" + (f"。{s['note']}" if s.get("note") else ""),
                                  "list-2026-10-30")).fetchone()["r"]
                if s.get("alt"):
                    conn.execute("update spots set alt_names = %s where id = %s", (s["alt"], r["id"]))
                conn.commit()
                res = f"登録した（{r['code']}）"
            else:
                res = "登録する（試し）"
            lines.append(f"| {s['no']} | {s['name']} | {s['category']} | {where} | {area or ''} | {nearby} | {res} |")
    md = "\n".join(lines) + "\n"
    (out / "summary.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
