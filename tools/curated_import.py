"""利用者のリストの場所を、OpenStreetMap の要素から位置を決めて手入力の地点として登録する（2026-10-30）

  python -m tools.curated_import --data data/curated/2026-10-30_list.json --dry-run   位置と近くの地点を出すだけ
  python -m tools.curated_import --data data/curated/2026-10-30_list.json --apply     create_manual_spot で登録する

位置の決め方（place）
  node             ノードの位置
  inside           面（閉じた way）の中の代表点
  middle           線の長さの半分の位置
  nearest_to_spot  名前が ways_named の道のうち、既存の地点 spot にいちばん近い位置
  road_crossing    名前が roads_a の道と roads_b の道が交わる点（複数あれば near_spot / near_address にいちばん近い点）
  footbridges_over 名前が roads の道の上を渡る歩道橋（名前のない歩道・階段の橋）。当たった橋ごとに地点を置く（2026-10-30）
  town_alley       住所が town の町にある細い道（20〜200m）。near_address の近くで長いものから count 本
  landuse_mesh     国土数値情報 土地利用細分メッシュ（2021年度・100m）で、code の土地利用が3×3マスに最も多く集まる場所。
                   near_address から radius_m 以内で、座標から引いた住所に town を含むもの。away_spot から away_m 以上離す
  address          国土地理院の住所検索で address の代表点
  point            利用者が指定した lat・lon（地図のURLなど。出典に url を残す）
外部：国土地理院の住所検索・逆ジオコーダー（1秒以上の間隔）、国土数値情報（出典：国土交通省 国土数値情報。加工して使用）。
決まり：位置は推測しない。同じ名前の地点が200m以内にあれば登録しない（何度実行しても同じ）。出典として OSM の要素のURLを残す。
"""
import argparse
import io
import json
import math
import time
import zipfile
from pathlib import Path

import requests

import osmium
from shapely.geometry import LineString, MultiPoint, Point, Polygon
from shapely.ops import nearest_points

from locadb import osm_source
from locadb.db import connect

SAME_NAME_M = 200
UA = "fukuoka-locadb/2.0 (https://github.com/mandrill-fukuoka/fukuoka-locadb; location research)"
GSI_SEARCH = "https://msearch.gsi.go.jp/address-search/AddressSearch"
GSI_REVERSE = "https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress"
L03B_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/L03-b/L03-b-21/L03-b-21_5030-jgd2011_GML.zip"
NARROW = {"footway", "path", "steps", "pedestrian", "living_street", "service"}
_last = [0.0]


def _get(url, **params):
    wait = 1.1 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    r = requests.get(url, params=params, timeout=60, headers={"User-Agent": UA})
    _last[0] = time.time()
    r.raise_for_status()
    return r.json()


def geocode(address):
    js = _get(GSI_SEARCH, q=address)
    return Point(*js[0]["geometry"]["coordinates"]) if js else None


def town_at(p):
    js = _get(GSI_REVERSE, lat=round(p.y, 6), lon=round(p.x, 6))
    return (js.get("results") or {}).get("lv01Nm") or ""


def dist_m(a, b):
    return math.hypot((a.x - b.x) * 92470, (a.y - b.y) * 111000)


_mesh = {}


def mesh_cells(code):
    """国土数値情報 L03-b（2021・5030）の、土地利用種別が code の100mマスの中心（経度・緯度）"""
    if code not in _mesh:
        import shapefile
        if "zip" not in _mesh:
            r = requests.get(L03B_URL, timeout=600, headers={"User-Agent": UA}); r.raise_for_status()
            _mesh["zip"] = zipfile.ZipFile(io.BytesIO(r.content))
        z = _mesh["zip"]
        sf = shapefile.Reader(shp=io.BytesIO(z.read("L03-b-21_5030.shp")), dbf=io.BytesIO(z.read("L03-b-21_5030.dbf")),
                              shx=io.BytesIO(z.read("L03-b-21_5030.shx")))
        cells = []
        for sr in sf.iterShapeRecords():
            if sr.record[1] == code:
                xs = [x for x, _ in sr.shape.points]; ys = [y for _, y in sr.shape.points]
                cells.append(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2))
        _mesh[code] = cells
    return _mesh[code]


class H(osmium.SimpleHandler):
    def __init__(self, want_ids: set, want_names: set):
        super().__init__()
        self.want_ids, self.want_names = want_ids, want_names
        self.geom = {}                      # "n123"/"w123" -> shapely（経度・緯度）
        self.named = {}                     # 名前 -> [LineString]
        self.footbridges = []               # 名前のない歩道・階段の橋（LineString）
        self.narrow = []                    # 細い道（LineString, way id）
        self.highways = []                  # 名前のある道（名前, LineString）。交差点が見つからないときの手がかり

    def node(self, n):
        k = f"n{n.id}"
        if k in self.want_ids:
            self.geom[k] = Point(n.location.lon, n.location.lat)

    def way(self, w):
        k = f"w{w.id}"
        nm = w.tags.get("name:ja") or w.tags.get("name")
        hw, br = w.tags.get("highway"), w.tags.get("bridge")
        underground = w.tags.get("tunnel") not in (None, "no") or w.tags.get("indoor") not in (None, "no") \
            or w.tags.get("layer", "0").startswith("-")
        is_fb = hw in ("footway", "path", "steps") and br not in (None, "no") and not nm
        is_narrow = hw in NARROW and not underground and br in (None, "no") and \
            (hw != "service" or w.tags.get("service") in (None, "alley"))
        is_named_road = bool(hw and nm and hw not in NARROW)
        if k not in self.want_ids and nm not in self.want_names and not is_fb and not is_narrow and not is_named_road:
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
        if is_fb:
            self.footbridges.append(LineString(cs))
        if is_narrow:
            self.narrow.append((LineString(cs), w.id))
        if is_named_road:
            self.highways.append((nm, LineString(cs)))


def near_point(spec, conn):
    """近さの基準：near_spot（既存の地点名）か near_address（住所）"""
    if spec.get("near_spot"):
        row = conn.execute("""select ST_X(geom::geometry) lon, ST_Y(geom::geometry) lat from spots
                               where name = %s and data_status <> 'archived' order by id limit 1""", (spec["near_spot"],)).fetchone()
        if row:
            return Point(row["lon"], row["lat"])
    if spec.get("near_address"):
        return geocode(spec["near_address"])
    return None


def resolve_many(spec, h, conn):
    """複数の地点を返す置き方。[(名前, Point, 補足)]"""
    place = spec["place"]
    if place == "footbridges_over":
        out = []
        for road in spec["roads"]:
            for rl in h.named.get(road, []):
                for fb in h.footbridges:
                    if fb.length > 0 and fb.intersects(rl):
                        p = fb.interpolate(0.5, normalized=True)
                        if all(dist_m(p, q) > 80 for _, q, _ in out):
                            out.append((road, p, ""))
        res = []
        for road, p, _ in out:
            t = town_at(p)
            res.append((f"{road}の歩道橋" + (f"（{t}）" if t else ""), p, f"{road}の上を渡る歩道橋（OSM の道と橋の形から）"))
        return res
    return [(spec["name"], resolve(spec, h, conn), spec.get("note", ""))]


def resolve(spec, h, conn):
    place = spec["place"]
    if place == "address":
        return geocode(spec["address"])
    if place == "point":
        return Point(spec["lon"], spec["lat"])
    if place == "road_crossing":
        a = [l for n in spec["roads_a"] for l in h.named.get(n, [])]
        b = [l for n in spec["roads_b"] for l in h.named.get(n, [])]
        pts = []
        for la in a:
            for lb in b:
                x = la.intersection(lb)
                if not x.is_empty:
                    pts += list(x.geoms) if hasattr(x, "geoms") else [x]
        pts = [p for p in pts if p.geom_type == "Point"]
        if not pts:
            ref = near_point(spec, conn)
            if ref is not None:                      # 手がかり：roads_a と交わる道の名前（近い順）
                near = {}
                for nm, l in h.highways:
                    for la in a:
                        x = la.intersection(l)
                        if not x.is_empty and nm not in spec["roads_a"]:
                            for q in (list(x.geoms) if hasattr(x, "geoms") else [x]):
                                if q.geom_type == "Point":
                                    d = dist_m(q, ref)
                                    if d <= 900 and d < near.get(nm, (1e9,))[0]:
                                        near[nm] = (d, q)
                spec["_hint"] = "、".join(f"{nm}（{int(d)}m）" for nm, (d, _) in sorted(near.items(), key=lambda x: x[1][0])[:10])
            return None
        ref = near_point(spec, conn)
        return min(pts, key=lambda p: dist_m(p, ref)) if ref else pts[0]
    if place == "town_alley":
        ref = near_point(spec, conn)
        if ref is None:
            return None
        def line_m(l):
            cs = list(l.coords)
            return sum(dist_m(Point(*cs[i]), Point(*cs[i + 1])) for i in range(len(cs) - 1))
        cands = [(line_m(l), l) for l, _ in h.narrow]
        cands = sorted([(m, l) for m, l in cands if 20 <= m <= 200
                        and dist_m(l.interpolate(0.5, normalized=True), ref) <= spec.get("radius_m", 400)],
                       key=lambda x: -x[0])
        for _, l in cands[:40]:
            p = l.interpolate(0.5, normalized=True)
            if spec["town"] in town_at(p):
                return p
        return None
    if place == "landuse_mesh":
        ref = near_point(spec, conn)
        if ref is None:
            return None
        rad = spec.get("radius_m", 1000)
        cells = [Point(x, y) for x, y in mesh_cells(spec["code"]) if dist_m(Point(x, y), ref) <= rad + 200]
        if not cells:
            return None
        key = {(round(c.x / 0.00125), round(c.y / 0.000833)) for c in cells}       # 100mマス（経度45秒/40・緯度30秒/40）
        def dens(c):
            i, j = round(c.x / 0.00125), round(c.y / 0.000833)
            return sum((i + di, j + dj) in key for di in (-1, 0, 1) for dj in (-1, 0, 1))
        away = None
        if spec.get("away_spot"):
            away = near_point({"near_spot": spec["away_spot"]}, conn)
        ranked = sorted([c for c in cells if dist_m(c, ref) <= rad and (away is None or dist_m(c, away) >= spec.get("away_m", 0))],
                        key=lambda c: (-dens(c), dist_m(c, ref)))
        for c in ranked[:40]:
            if spec["town"] in town_at(c):
                return c
        return None
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
    names = {s["ways_named"] for s in data["spots"] if s.get("ways_named")}
    for s in data["spots"]:
        names |= set(s.get("roads", [])) | set(s.get("roads_a", [])) | set(s.get("roads_b", []))
    h = H({s["osm"] for s in data["spots"] if s.get("osm")}, names)
    h.apply_file(str(clipped), locations=True, idx="flex_mem"); clipped.unlink()

    lines = ["| No | 名前 | 種別 | 位置 | 区 | 近い地点（m） | 結果 |", "|---|---|---|---|---|---|---|"]
    with connect() as conn:
        items = []
        for s0 in data["spots"]:
            for nm, p, note in resolve_many(s0, h, conn):
                items.append(({**s0, "name": nm, "note": note}, p))
            if s0.get("_hint"):
                lines.append(f"| {s0['no']} | （手がかり）{s0['name']} | | | | {s0['_hint']} | 交わる道の名前（近い順） |")
        for s, p in items:
            if p is None:
                lines.append(f"| {s['no']} | {s['name']} | {s['category']} | - | - | - | ！位置を決められない（OSMの要素・データが見つからない） |"); continue
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
                src = s["url"] if s.get("url") else f"https://www.openstreetmap.org/{ {'n': 'node', 'w': 'way'}[s['osm'][0]] }/{s['osm'][1:]}" if s.get("osm") \
                    else "https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-L03-b-2021.html" if s["place"] == "landuse_mesh" \
                    else f"https://www.openstreetmap.org/#map=18/{lat}/{lon}"
                how = {"landuse_mesh": "国土数値情報 土地利用細分メッシュ（2021年度）を加工して位置を決めた",
                       "address": "国土地理院の住所検索の代表点",
                       "point": "利用者が地図で指定した位置",
                       "footbridges_over": "OpenStreetMap の道と歩道橋の形から位置を決めた",
                       "road_crossing": "OpenStreetMap の道の交わる点"}.get(s["place"], "OpenStreetMap の要素から位置を決めた")
                r = conn.execute("select create_manual_spot(%s,%s,%s,%s,'{}',null,'unverified',%s,%s,'official',null,%s) r",
                                 (s["name"], lat, lon, s["category"], src,
                                  f"{how}（利用者の地点リスト 2026-10-30）" + (f"。{s['note']}" if s.get("note") else ""),
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
