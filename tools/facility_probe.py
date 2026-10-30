"""福岡市の道路施設一覧（トンネル・アンダーパス・横断歩道橋・地下横断施設など）の位置を OpenStreetMap から探す（読み取りだけ）

  python -m tools.facility_probe --data data/curated/fukuoka_road_facilities.json --out DIR

一覧（PDF）には位置がないので、次のどちらかで位置を決める。決められないものは「未決」として残す（推測しない）。
  1. 名前の一致：OSM の name・bridge:name・tunnel:name・alt_name・official_name・old_name が施設名（括弧・空白を除く）と同じ
  2. 長さの一致：hint（町名）を国土地理院で引いた位置から 700m 以内に、同じ種類の構造物があり、長さが一覧の延長の ±35% に
     入るものが1つだけ
出力：facilities.csv（決め方・位置・OSM の要素・いちばん近い既存の地点）と summary.md、resolved.json（curated_import の入力）
"""
import argparse
import csv
import json
import math
import re
import unicodedata
from pathlib import Path

import osmium
from shapely.geometry import LineString, Point

from locadb import osm_source
from locadb.db import connect
from tools.curated_import import dist_m, geocode

NEAR_M = 700
LEN_TOL = 0.35
NAME_KEYS = ("name", "name:ja", "bridge:name", "tunnel:name", "alt_name", "official_name", "old_name", "loc_name")
CATEGORY = {"tunnel": "underpass", "underpass": "underpass", "underground": "underpass", "footbridge": "bridge_overpass",
            "gantry": "gantry_sign", "other": None}
KIND_LABEL = {"tunnel": "トンネル", "underpass": "アンダーパス", "underground": "地下横断施設", "footbridge": "横断歩道橋",
              "gantry": "門型標識", "other": "その他構造物"}
CAR = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "motorway_link",
       "trunk_link", "primary_link", "secondary_link", "tertiary_link", "service"}
FOOT = {"footway", "path", "steps", "pedestrian", "cycleway"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    return re.sub(r"[\s・_]", "", s)


def kind_of(t: dict) -> str | None:
    hw = t.get("highway")
    tunnel = t.get("tunnel") not in (None, "no")
    under = tunnel or t.get("layer", "0").startswith("-") or t.get("covered") == "yes"
    bridge = t.get("bridge") not in (None, "no")
    if hw in CAR and tunnel:
        return "tunnel"
    if hw in CAR and under:
        return "underpass"
    if hw in FOOT and under:
        return "underground"
    if hw in FOOT and bridge:
        return "footbridge"
    if t.get("man_made") == "bridge" or (t.get("bridge") and not hw):
        return "footbridge"
    return None


class H(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.rows = []      # (key, names(set), kind, LineString/Point, length_m)

    def _add(self, key, t, geom, length):
        names = {norm(t[k]) for k in NAME_KEYS if t.get(k)}
        k = kind_of(t)
        if names or k:
            self.rows.append((key, names, k, geom, length))

    def node(self, n):
        if len(n.tags) and any(n.tags.get(k) for k in NAME_KEYS):
            t = {x.k: x.v for x in n.tags}
            self._add(f"n{n.id}", t, Point(n.location.lon, n.location.lat), 0.0)

    def way(self, w):
        if not len(w.tags):
            return
        t = {x.k: x.v for x in w.tags}
        if not (any(t.get(k) for k in NAME_KEYS) or kind_of(t)):
            return
        try:
            cs = [(nd.location.lon, nd.location.lat) for nd in w.nodes]
        except osmium.InvalidLocationError:
            return
        if len(cs) < 2:
            return
        ln = sum(dist_m(Point(*cs[i]), Point(*cs[i + 1])) for i in range(len(cs) - 1))
        self._add(f"w{w.id}", t, LineString(cs), ln)


def mid(g):
    return g.interpolate(0.5, normalized=True) if g.geom_type == "LineString" else g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="facility-out")
    a = ap.parse_args()
    data = json.loads(Path(a.data).read_text(encoding="utf-8"))
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    full, clipped = out / "kyushu.osm.pbf", out / "fukuoka.osm.pbf"
    osm_source.download(full); osm_source.clip(full, clipped); full.unlink()
    h = H(); h.apply_file(str(clipped), locations=True, idx="flex_mem"); clipped.unlink()

    res, resolved = [], []
    with connect() as conn:
        for f in data["facilities"]:
            want = norm(f["name"])
            how, pick, note = "", None, ""
            named = [r for r in h.rows if want and want in r[1]]
            if named:
                pick = max(named, key=lambda r: r[4]); how = "名前の一致"
            elif f.get("hint") and f.get("length_m"):
                c = geocode(f["hint"])
                if c is not None:
                    cand = [r for r in h.rows if r[2] == f["kind"] and dist_m(mid(r[3]), c) <= NEAR_M
                            and abs(r[4] - f["length_m"]) <= f["length_m"] * LEN_TOL]
                    if len(cand) == 1:
                        pick = cand[0]; how = "長さの一致"
                    elif cand:
                        how = "未決"; note = f"長さの合う候補が{len(cand)}件（" + "、".join(f"{r[0]} {int(r[4])}m" for r in cand[:6]) + "）"
                    else:
                        how = "未決"; note = "町名の近くに、種類と長さの合う構造物がない"
                else:
                    how = "未決"; note = "町名を引けない"
            else:
                how = "未決"; note = "名前がOSMになく、町名の手がかりもない" if not f.get("hint") else "延長の記載がない"
            row = {"kind": KIND_LABEL[f["kind"]], "ward": f["ward"], "name": f["name"], "length_m": f.get("length_m"),
                   "how": how, "note": note}
            if pick:
                p = mid(pick[3])
                near = conn.execute("""select s.name, sc.name cat, round(ST_Distance(s.geom, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography)) d
                                         from spots s join spot_categories sc on sc.code = s.category where s.data_status <> 'archived'
                                        order by s.geom <-> ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography limit 1""",
                                    (p.x, p.y, p.x, p.y)).fetchone()
                same = conn.execute("""select s.name from spots s where s.data_status <> 'archived' and s.name = %s
                                          and ST_DWithin(s.geom, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, 300)""",
                                    (f["name"], p.x, p.y)).fetchone()
                ward_ok = conn.execute("select area_at(%s,%s) a", (p.y, p.x)).fetchone()["a"]
                row.update({"osm": pick[0], "osm_len_m": round(pick[4], 1), "lat": round(p.y, 6), "lon": round(p.x, 6),
                            "near": f"{near['name']}〔{near['cat']}〕（{int(near['d'])}m）" if near else "",
                            "exists": "登録済み" if same else "", "in_city": ward_ok or ""})
                if not same and ward_ok:
                    resolved.append({"no": [len(resolved) + 1], "name": f["name"], "category": CATEGORY[f["kind"]] or "bridge_overpass",
                                     "osm": pick[0], "place": "node" if pick[0].startswith("n") else "middle",
                                     "note": f"{KIND_LABEL[f['kind']]}（福岡市の道路施設一覧。延長 {f.get('length_m')}m、{f.get('route')}）。"
                                             f"位置は {how}（OSM {pick[0]}、長さ {round(pick[4])}m）"})
            res.append(row)
    cols = ["kind", "ward", "name", "length_m", "how", "osm", "osm_len_m", "lat", "lon", "near", "exists", "in_city", "note"]
    with open(out / "facilities.csv", "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(res)
    (out / "resolved.json").write_text(json.dumps({"note": data.get("source", ""), "spots": resolved}, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    from collections import Counter
    c = Counter(r["how"] for r in res)
    lines = [f"名前の一致 {c['名前の一致']}／長さの一致 {c['長さの一致']}／未決 {c['未決']}（全 {len(res)}）。"
             f"登録済み {sum(1 for r in res if r.get('exists'))}、新しく登録できる {len(resolved)}", "",
             "| 種類 | 区 | 施設名 | 延長m | 決め方 | OSM | OSMの長さm | 近い地点 | 備考 |", "|---|---|---|---|---|---|---|---|---|"]
    for r in res:
        lines.append(f"| {r['kind']} | {r['ward']} | {r['name']} | {r['length_m'] or ''} | {r['how']} | {r.get('osm', '')} | "
                     f"{r.get('osm_len_m', '')} | {r.get('near', '')} | {r.get('exists', '')}{r['note']} |")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
