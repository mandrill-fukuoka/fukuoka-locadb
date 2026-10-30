"""利用者の地点リスト（2026-10-30「66カットDB照合」）で △・× だった場所を、OpenStreetMap から探す（読み取りだけ。DBへは SELECT のみ）

  python -m tools.target_probe --out DIR

それぞれの場所について、名前・道の名前・範囲（田園・住宅地など）の当たりを出し、いちばん近い既存の地点との距離を付ける。
位置は推測しない（OSM の要素の位置だけを出す）。追加するかどうかは人が決める。
"""
import argparse
import csv
import json
import math
import re
from pathlib import Path

import osmium

from locadb import osm_source
from locadb.db import connect

# (番号, 場所, 名前の正規表現, 要素の条件, 目印の地名（範囲の候補を探すとき）)
#  条件：name＝名前で探す / farmland＝目印の近くの田畑 / residential＝目印の近くの住宅地 / alley＝目印の近くの細い道
#        footbridge＝目印の近くの歩道橋 / road＝道の名前 / terminal＝空港ターミナル
TARGETS = [
    (1, "西新・高取の商店街", r"(西新|高取|藤崎).*(商店街|商店会|名店街)|サザエさん通り|リヤカー部隊", "name", None),
    (4, "天神交差点・ONE FUKUOKA前", r"ONE FUKUOKA|ワン.?フクオカ|天神交差点|^天神$", "name", None),
    (10, "早良区脇山の田園", None, "farmland", "脇山"),
    (11, "西区元岡の農道", None, "farmland", "元岡"),
    (12, "今宿周辺の田園", None, "farmland", "今宿"),
    (14, "大名・今泉の路地", None, "alley", "大名|今泉"),
    (18, "福岡空港（ターミナル・滑走路側）", r"福岡空港.*(ターミナル|展望)|国内線旅客ターミナル|国際線旅客ターミナル", "terminal", None),
    (21, "高宮の住宅街", None, "residential", "高宮"),
    (23, "屋台・天神", r"屋台", "name_near", "天神|渡辺通"),
    (24, "屋台・長浜", r"長浜.*屋台|屋台.*長浜", "name", None),
    (25, "ONE FUKUOKA BLDG.", r"ONE FUKUOKA|ワンフクオカ|ワン・フクオカ", "name", None),
    (26, "天神ビジネスセンター（Ⅱ）", r"天神ビジネスセンター", "name", None),
    (33, "志賀島の海岸道路", r"志賀島循環|県道542|志賀島.*線", "road", None),
    (50, "天神・博多の大通りの歩道橋", None, "footbridge", "天神|博多駅"),
    (51, "博多駅周辺の高架", r"鹿児島本線|新幹線|篠栗線|博多南線", "rail_bridge", "博多駅"),
    (53, "博多駅の地下街", r"博多駅地下街|博多地下街|デイトス|博多駅.*地下", "name", None),
    (54, "キャナルシティ博多", r"キャナルシティ", "name", None),
    (58, "天神・警固の交差点", r"警固|天神西通り|国体道路", "name", None),
    (59, "博多駅前の大通り（大博通り）", r"大博通り|住吉通り|博多駅前通り", "road", None),
    (60, "親不孝通り", r"親不孝通り|親富孝通り|親富孝|親不孝", "name", None),
    (61, "中洲の裏路地", None, "alley", "中洲"),
    (64, "博多港の岸壁・倉庫街（中央ふ頭）", r"中央ふ頭|中央埠頭|博多港.*(岸壁|上屋)|那の津.*上屋", "name", None),
]
NEAR_ANCHOR_M = {"farmland": 1500, "residential": 700, "alley": 450, "footbridge": 700, "rail_bridge": 800, "name_near": 900}


def dist_m(a, b):
    return math.hypot((a[0] - b[0]) * 111000, (a[1] - b[1]) * 111000 * math.cos(math.radians(a[0])))


class H(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.places = []       # (name, lat, lon)
        self.rows = []         # dict(type,id,name,tags,lat,lon,area_m2,len_m)

    def _row(self, typ, oid, t, locs):
        lat = sum(a for a, _ in locs) / len(locs)
        lon = sum(b for _, b in locs) / len(locs)
        area = 0.0
        if len(locs) >= 4 and locs[0] == locs[-1]:
            xs = [(b - lon) * 92470 for _, b in locs]; ys = [(a - lat) * 111000 for a, _ in locs]
            area = abs(sum(xs[i] * ys[i + 1] - xs[i + 1] * ys[i] for i in range(len(xs) - 1))) / 2
        ln = sum(dist_m(locs[i], locs[i + 1]) for i in range(len(locs) - 1))
        self.rows.append({"type": typ, "id": oid, "name": t.get("name:ja") or t.get("name") or "", "tags": t,
                          "lat": lat, "lon": lon, "area_m2": round(area), "len_m": round(ln)})

    def node(self, n):
        if not len(n.tags):
            return
        t = {x.k: x.v for x in n.tags}
        if t.get("place") and (t.get("name:ja") or t.get("name")):
            self.places.append((t.get("name:ja") or t.get("name"), n.location.lat, n.location.lon))
        if t.get("name") or t.get("highway") == "traffic_signals":
            self._row("n", n.id, t, [(n.location.lat, n.location.lon)])

    def way(self, w):
        if not len(w.tags):
            return
        t = {x.k: x.v for x in w.tags}
        keep = (t.get("name") or t.get("landuse") in ("farmland", "paddy", "residential", "meadow")
                or t.get("highway") in ("service", "footway", "living_street", "path", "steps", "pedestrian", "track")
                or t.get("railway") == "rail" or t.get("aeroway"))
        if not keep:
            return
        try:
            locs = [(nd.location.lat, nd.location.lon) for nd in w.nodes]
        except osmium.InvalidLocationError:
            return
        if locs:
            self._row("w", w.id, t, locs)


def match(row, kind, rx):
    t, nm = row["tags"], row["name"]
    if kind in ("name", "name_near"):
        return bool(nm and re.search(rx, nm))
    if kind == "road":
        return bool(t.get("highway") and nm and re.search(rx, nm))
    if kind == "terminal":
        return bool((t.get("aeroway") in ("terminal", "apron", "runway") or (nm and re.search(rx, nm))) and
                    (t.get("aeroway") or re.search(rx, nm or "")))
    if kind == "farmland":
        return t.get("landuse") in ("farmland", "paddy", "meadow") and row["area_m2"] >= 5000
    if kind == "residential":
        return t.get("landuse") == "residential" and row["area_m2"] >= 5000
    if kind == "alley":
        return (t.get("highway") in ("living_street", "footway", "path", "steps", "pedestrian")
                or (t.get("highway") == "service" and t.get("service") in ("alley", None))) \
            and t.get("tunnel") in (None, "no") and t.get("indoor") in (None, "no") and 25 <= row["len_m"] <= 400
    if kind == "footbridge":
        return t.get("highway") in ("footway", "path", "steps") and t.get("bridge") not in (None, "no")
    if kind == "rail_bridge":
        return t.get("railway") == "rail" and t.get("bridge") not in (None, "no") and nm and re.search(rx, nm)
    return False


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="target-out"); a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    full, clipped = out / "kyushu.osm.pbf", out / "fukuoka.osm.pbf"
    osm_source.download(full); osm_source.clip(full, clipped); full.unlink()
    h = H(); h.apply_file(str(clipped), locations=True, idx="flex_mem"); clipped.unlink()

    result = []
    with connect() as conn:
        for no, label, rx, kind, anchor in TARGETS:
            anchors = [(n, la, lo) for n, la, lo in h.places if anchor and re.fullmatch(f"(?:{anchor}).*", n)]
            hits = [r for r in h.rows if match(r, kind, rx)]
            if anchor:
                lim = NEAR_ANCHOR_M.get(kind, 1000)
                hits = [dict(r, anchor=n, anchor_m=round(dist_m((r["lat"], r["lon"]), (la, lo))))
                        for r in hits for n, la, lo in [min(anchors, key=lambda p: dist_m((r["lat"], r["lon"]), (p[1], p[2])))]
                        if anchors and dist_m((r["lat"], r["lon"]), (la, lo)) <= lim] if anchors else []
            key = (lambda r: -r["area_m2"]) if kind in ("farmland", "residential") else \
                  (lambda r: r.get("anchor_m", 0) - r["len_m"] / 10) if kind in ("alley", "footbridge", "rail_bridge", "name_near") else \
                  (lambda r: -r["len_m"])
            hits = sorted(hits, key=key)[:8]
            for r in hits:
                nb = conn.execute("""select s.name, sc.name cat, round(ST_Distance(s.geom, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography)) d
                                       from spots s join spot_categories sc on sc.code = s.category
                                      where s.data_status <> 'archived'
                                      order by s.geom <-> ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography limit 1""",
                                  (r["lon"], r["lat"], r["lon"], r["lat"])).fetchone()
                area = conn.execute("select area_at(%s,%s) a", (r["lat"], r["lon"])).fetchone()["a"]
                tags = {k: v for k, v in r["tags"].items() if k in ("highway", "landuse", "building", "railway", "bridge", "aeroway",
                                                                   "amenity", "shop", "service", "crop", "building:levels", "layer")}
                result.append({"no": no, "place": label, "osm": f"{r['type']}{r['id']}", "name": r["name"],
                               "tags": json.dumps(tags, ensure_ascii=False), "lat": round(r["lat"], 6), "lon": round(r["lon"], 6),
                               "area_m2": r["area_m2"], "len_m": r["len_m"], "anchor": r.get("anchor", ""), "anchor_m": r.get("anchor_m", ""),
                               "in_city": area or "", "near_spot": nb["name"] if nb else "", "near_cat": nb["cat"] if nb else "",
                               "near_m": nb["d"] if nb else ""})
            if not hits:
                result.append({"no": no, "place": label, "osm": "", "name": "（OSMに見つからない）"})
    cols = ["no", "place", "osm", "name", "tags", "lat", "lon", "area_m2", "len_m", "anchor", "anchor_m", "in_city", "near_spot", "near_cat", "near_m"]
    with open(out / "targets.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(result)
    lines = ["| No | 場所 | OSM | 名前 | タグ | 面積㎡ | 長さm | 区 | 近い地点（m） |", "|---|---|---|---|---|---|---|---|---|"]
    for r in result:
        lines.append(f"| {r['no']} | {r['place']} | {r.get('osm','')} | {r['name']} | {r.get('tags','')} | {r.get('area_m2','')} | "
                     f"{r.get('len_m','')} | {r.get('in_city','')} | {r.get('near_spot','')}（{r.get('near_m','')}） |")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
