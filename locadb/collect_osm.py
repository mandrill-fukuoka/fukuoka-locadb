"""OpenStreetMap（Overpass API）から候補地を収集し raw_candidates に登録する

使い方: python -m locadb.collect_osm --area fuk-chuo
OSMデータは ODbL ライセンス。DB内に出典(source_ref)を必ず保持する。
"""
import argparse
import json
import time

import requests

from .db import connect, job

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

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
    "river":           ['way["waterway"~"^(river|canal)$"]["name"]'],
    "coast":           ['nwr["natural"~"^(beach|coastline)$"]["name"]', 'nwr["natural"="wetland"]["wetland"="tidalflat"]'],
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


def build_query(area_name: str, filters: list[str]) -> str:
    body = "\n".join(f"  {f}(area.a);" for f in filters)
    return f"""
[out:json][timeout:120];
area["name"="福岡市"]["admin_level"="7"]->.city;
rel(area.city)["boundary"="administrative"]["name"="{area_name}"];
map_to_area->.a;
(
{body}
);
out center tags;
"""


def fetch(query: str) -> list[dict]:
    for attempt in range(4):
        r = requests.post(OVERPASS_URL, data={"data": query}, timeout=180,
                          headers={"User-Agent": "fukuoka-locadb/0.1 (location research)"})
        if r.status_code == 200:
            return r.json().get("elements", [])
        time.sleep(10 * (attempt + 1))  # 429/504 対策
    r.raise_for_status()
    return []


def point_of(el: dict):
    if el["type"] == "node":
        return el["lon"], el["lat"]
    c = el.get("center")
    return (c["lon"], c["lat"]) if c else None


def run(area_code: str, categories: list[str] | None = None) -> dict:
    area_name = AREA_NAMES[area_code]
    cats = categories or list(CATEGORY_QUERIES)
    with connect() as conn, job(conn, f"collect_osm:{area_code}") as stats:
        for cat in cats:
            els = fetch(build_query(area_name, CATEGORY_QUERIES[cat]))
            n = 0
            for el in els:
                pt = point_of(el)
                if not pt:
                    continue
                tags = el.get("tags", {})
                conn.execute(
                    """insert into raw_candidates (source, source_ref, name, geom, raw, category_hint)
                       values ('osm', %s, %s, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, %s, %s)
                       on conflict (source, source_ref) do update
                         set raw = excluded.raw, name = excluded.name, fetched_at = now()""",
                    (f"osm:{el['type']}/{el['id']}", tags.get("name:ja") or tags.get("name"),
                     pt[0], pt[1], json.dumps({"area": area_code, "tags": tags}, ensure_ascii=False), cat),
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
