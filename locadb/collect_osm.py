"""OpenStreetMap（Overpass API）から候補地を収集し raw_candidates に登録する

使い方: python -m locadb.collect_osm --area fuk-chuo
OSMデータは ODbL ライセンス。DB内に出典(source_ref)を必ず保持する。
"""
import argparse
import json
import time
from math import asin, cos, radians, sin, sqrt

import requests

from .db import connect, job

# 公開Overpassサーバー（混雑時は順に切り替え）
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# エリアコード → OSMの区名
AREA_NAMES = {
    "fuk-chuo": "中央区", "fuk-hakata": "博多区", "fuk-higashi": "東区",
    "fuk-sawara": "早良区", "fuk-nishi": "西区", "fuk-minami": "南区", "fuk-jonan": "城南区",
}

# カテゴリ → Overpass フィルタ（nwr = node/way/relation）
CATEGORY_QUERIES = {
    "school":          ['nwr["amenity"~"^(school|university|college)$"]'],
    "hospital":        ['nwr["amenity"~"^(hospital|clinic|nursing_home)$"]["name"]', 'nwr["healthcare"="hospital"]'],
    "civic":           ['nwr["amenity"~"^(townhall|library|theatre|arts_centre|community_centre)$"]'],
    "housing_estate":  ['way["building"="apartments"]["name"~"団地|住宅|ハイツ|コーポ"]', 'way["landuse"="residential"]["name"~"団地"]'],
    "shotengai":       ['way["highway"]["name"~"商店街"]', 'nwr["name"~"商店街"]["shop"!~"."]'],
    "eatery_street":   ['nwr["amenity"="marketplace"]', 'way["name"~"横丁|屋台"]'],
    "commercial_facility": ['nwr["shop"="mall"]', 'nwr["shop"="department_store"]'],
    "station":         ['nwr["railway"="station"]', 'nwr["public_transport"="station"]'],
    "bridge_overpass": ['way["bridge"="yes"]["name"]', 'way["highway"="footway"]["bridge"="yes"]["name"]'],
    "factory":         ['nwr["man_made"="works"]', 'way["landuse"="industrial"]["name"]'],
    "warehouse":       ['way["building"="warehouse"]["name"]', 'way["landuse"="industrial"]["industrial"~"warehouse|logistics"]'],
    "port":            ['nwr["landuse"="port"]', 'nwr["man_made"~"^(pier|quay)$"]', 'nwr["harbour"]'],
    "river":           ['way["waterway"~"^(river|canal)$"]["name"]', 'nwr["natural"="water"]["water"~"^(river|canal|oxbow)$"]["name"]'],
    "coast":           ['nwr["natural"="coastline"]["name"]', 'nwr["natural"="wetland"]["wetland"="tidalflat"]',
                        'nwr["man_made"~"^(breakwater|groyne)$"]["name"]', 'nwr["natural"~"^(cape|bay)$"]["name"]'],
    "beach":           ['nwr["natural"="beach"]', 'nwr["leisure"="beach_resort"]'],
    "pond":            ['nwr["natural"="water"]["water"~"^(pond|lake|reservoir|moat|lagoon)$"]["name"]',
                        'nwr["landuse"="reservoir"]["name"]', 'nwr["natural"="water"][!"water"]["name"~"池|湖|堀"]'],
    "fishing_port":    ['nwr["harbour"]["name"~"漁港"]', 'nwr["name"~"漁港"]'],
    "island":          ['nwr["place"~"^(island|islet)$"]'],
    "park":            ['nwr["leisure"~"^(park|garden)$"]["name"]'],
    "hill":            ['nwr["natural"="peak"]', 'nwr["tourism"="viewpoint"]'],
    "shrine_temple":   ['nwr["amenity"="place_of_worship"]["name"]'],
    "heritage":        ['nwr["historic"]["name"]', 'nwr["heritage"]'],
    "new_building":    ['way["building"]["building:levels"~"^([2-9][0-9])$"]', 'nwr["tourism"="hotel"]["building:levels"~"^([1-9][0-9])$"]'],
    "public_space":    ['nwr["place"="square"]', 'way["highway"="pedestrian"]["name"]'],
    "vacant":          ['nwr["disused:shop"]', 'nwr["abandoned:building"]', 'way["building"]["disused"="yes"]'],
    "redevelopment_site": ['way["landuse"~"^(construction|brownfield)$"]'],
    "railway":         ['way["railway"="rail"]["name"]'],
}


# 細長いもの（川・海岸線・線路・商店街・歩行者道）は、線に沿って一定間隔で地点を置く。
# 囲む四角形の中心を使うと、曲がった川などでは中心が川から外れてしまうため。
LINEAR_SPACING_M = {
    "river": 400, "coast": 400, "railway": 600, "shotengai": 150, "public_space": 150, "bridge_overpass": 0,
}
MAX_POINTS_PER_FEATURE = 80


def build_query(area_name: str, filters: list[str], geom: bool = False) -> str:
    body = "\n".join(f"  {f}(area.a);" for f in filters)
    return f"""
[out:json][timeout:180];
area["name"="福岡市"]["admin_level"="7"]->.city;
rel(area.city)["boundary"="administrative"]["name"="{area_name}"];
map_to_area->.a;
(
{body}
);
out {"geom" if geom else "center"} tags;
"""


def _dist(a, b) -> float:
    """(lon, lat) 2点間の距離[m]"""
    dlat, dlon = radians(b[1] - a[1]), radians(b[0] - a[0])
    h = sin(dlat / 2) ** 2 + cos(radians(a[1])) * cos(radians(b[1])) * sin(dlon / 2) ** 2
    return 2 * 6371000 * asin(sqrt(h))


def sample_line(coords: list, spacing: float) -> list:
    """線に沿って spacing[m] ごとの点を返す。spacing=0 は線上の中央付近の1点のみ"""
    if not coords:
        return []
    if len(coords) == 1:
        return [coords[0]]
    seg = [_dist(coords[i], coords[i + 1]) for i in range(len(coords) - 1)]
    total = sum(seg)
    if total == 0:
        return [coords[0]]
    if spacing <= 0 or total <= spacing:
        targets = [total / 2]
    else:
        n = min(int(total // spacing), MAX_POINTS_PER_FEATURE)
        step = total / (n + 1) if n > 0 else total / 2
        targets = [step * (k + 1) for k in range(max(n, 1))]
    out, acc, i = [], 0.0, 0
    for t in targets:
        while i < len(seg) - 1 and acc + seg[i] < t:
            acc += seg[i]; i += 1
        f = 0 if seg[i] == 0 else (t - acc) / seg[i]
        a, b = coords[i], coords[i + 1]
        out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    return out


def points_of(el: dict, spacing: float | None):
    """要素から地点の座標リストを返す。[(lon, lat, ref_suffix), ...]"""
    if el["type"] == "node":
        return [(el["lon"], el["lat"], "")]
    if spacing is not None:
        lines = []
        if el["type"] == "way" and el.get("geometry"):
            lines.append([(g["lon"], g["lat"]) for g in el["geometry"] if g])
        elif el["type"] == "relation":
            for m in el.get("members", []):
                if m.get("type") == "way" and m.get("geometry"):
                    lines.append([(g["lon"], g["lat"]) for g in m["geometry"] if g])
        pts = []
        for line in lines:
            pts.extend(sample_line(line, spacing))
        pts = pts[:MAX_POINTS_PER_FEATURE]
        if pts:
            return [(lon, lat, f"@{k}") for k, (lon, lat) in enumerate(pts)]
    c = el.get("center")
    return [(c["lon"], c["lat"], "")] if c else []


def fetch(query: str) -> list[dict] | None:
    """取得できなければ None（その種別は今回スキップし、登録済みデータは残す）"""
    for attempt in range(3):
        for url in OVERPASS_URLS:
            try:
                r = requests.post(url, data={"data": query}, timeout=200,
                                  headers={"User-Agent": "fukuoka-locadb/0.1 (location research)"})
                if r.status_code == 200:
                    return r.json().get("elements", [])
                print(f"    {url.split('/')[2]}: HTTP {r.status_code}")
            except (requests.RequestException, ValueError) as e:
                print(f"    {url.split('/')[2]}: {type(e).__name__}")
        time.sleep(20 * (attempt + 1))  # 混雑時は待ってから再試行
    return None


def run(area_code: str, categories: list[str] | None = None) -> dict:
    area_name = AREA_NAMES[area_code]
    cats = categories or list(CATEGORY_QUERIES)
    with connect() as conn, job(conn, f"collect_osm:{area_code}") as stats:
        for cat in cats:
            spacing = LINEAR_SPACING_M.get(cat)
            els = fetch(build_query(area_name, CATEGORY_QUERIES[cat], geom=spacing is not None))
            if els is None:
                print(f"  {cat}: 取得失敗（サーバー混雑）→ 今回はスキップ")
                stats.setdefault("skipped", []).append(cat)
                continue
            n = 0
            placed: dict[str, list] = {}   # 同じ名前の川などが細切れでも、近すぎる点は置かない
            for el in els:
                tags = el.get("tags", {})
                raw = json.dumps({"area": area_code, "tags": tags}, ensure_ascii=False)
                nm = tags.get("name:ja") or tags.get("name") or ""
                for lon, lat, suffix in points_of(el, spacing):
                    if spacing and nm:
                        near = [p for p in placed.get(nm, []) if _dist(p, (lon, lat)) < spacing * 0.5]
                        if near:
                            continue
                        placed.setdefault(nm, []).append((lon, lat))
                    conn.execute(
                        """insert into raw_candidates (source, source_ref, name, geom, raw, category_hint)
                           values ('osm', %s, %s, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, %s, %s)
                           on conflict (source, source_ref) do update
                             set raw = excluded.raw, name = excluded.name, fetched_at = now()""",
                        (f"osm:{el['type']}/{el['id']}{suffix}", tags.get("name:ja") or tags.get("name"),
                         lon, lat, raw, cat),
                    )
                    n += 1
            conn.commit()
            stats[cat] = n
            print(f"  {cat}: {n}件")
            time.sleep(2)  # Overpassへの負荷配慮
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="fuk-chuo", choices=AREA_NAMES)
    ap.add_argument("--category", action="append")
    a = ap.parse_args()
    run(a.area, a.category)
