"""OpenStreetMap にある飲食店・カフェ・バー・服飾店などが福岡市7区にどれだけあるかを数える（読み取りだけ。DBへは SELECT のみ）

  python tools/osm_shops_probe.py --out DIR
  Geofabrik の九州の extract を取り、福岡市の範囲に切り出して、店舗の点・面を業態別に数える。
  チェーン判定：brand / brand:wikidata / operator がある、または同じ名前が3件以上ある。
決まり：外部への取得は pipeline と同じ（Geofabrik の公式 extract。MD5 を照合）。DBは変更しない。
"""
import argparse
import collections
import csv
import json
from pathlib import Path

import osmium

from locadb import osm_source
from locadb.db import connect

KINDS = {
    "restaurant": ("飲食店", {"amenity": {"restaurant", "fast_food", "food_court"}}),
    "cafe": ("カフェ", {"amenity": {"cafe", "ice_cream"}}),
    "bar": ("バー・居酒屋", {"amenity": {"bar", "pub", "biergarten"}}),
    "fashion": ("服飾店", {"shop": {"clothes", "shoes", "boutique", "jewelry", "bag", "fashion_accessories", "fashion", "tailor", "watches"}}),
    "other_shop": ("その他の店", {"shop": {"bakery", "books", "florist", "second_hand", "antiques", "furniture", "interior_decoration",
                                              "hairdresser", "beauty", "variety_store", "department_store", "mall", "art", "music", "records"}}),
}


def kind_of(t):
    for k, (_, rules) in KINDS.items():
        for key, vals in rules.items():
            if t.get(key) in vals:
                return k
    return None


class H(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.rows = []

    def _add(self, typ, oid, lat, lon, t):
        k = kind_of(t)
        if k and lat is not None:
            self.rows.append({"type": typ, "id": oid, "lat": lat, "lon": lon, "kind": k, "tag": t.get("amenity") or t.get("shop"),
                              "name": t.get("name:ja") or t.get("name") or "", "brand": t.get("brand") or t.get("brand:wikidata") or "",
                              "operator": t.get("operator") or "", "cuisine": t.get("cuisine") or "",
                              "hours": bool(t.get("opening_hours")), "web": bool(t.get("website") or t.get("contact:website")),
                              "outdoor": t.get("outdoor_seating") or ""})

    def node(self, n):
        if len(n.tags):
            self._add("n", n.id, n.location.lat, n.location.lon, {x.k: x.v for x in n.tags})

    def way(self, w):
        if len(w.tags) and kind_of({x.k: x.v for x in w.tags}):
            try:
                locs = [(nd.location.lat, nd.location.lon) for nd in w.nodes]
            except osmium.InvalidLocationError:
                return
            if locs:
                self._add("w", w.id, sum(a for a, _ in locs) / len(locs), sum(b for _, b in locs) / len(locs), {x.k: x.v for x in w.tags})


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="shops-out"); a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    full, clipped = out / "kyushu.osm.pbf", out / "fukuoka.osm.pbf"
    osm_source.download(full); osm_source.clip(full, clipped); full.unlink()
    h = H(); h.apply_file(str(clipped), locations=True, idx="flex_mem"); clipped.unlink()
    rows = h.rows
    with connect() as conn:
        for r in rows:
            r["area"] = conn.execute("select area_at(%s, %s) a", (r["lat"], r["lon"])).fetchone()["a"]
    rows = [r for r in rows if r["area"]]                          # 福岡市7区の中だけ
    names = collections.Counter(r["name"] for r in rows if r["name"])
    for r in rows:
        r["chain"] = bool(r["brand"] or r["operator"] or (r["name"] and names[r["name"]] >= 3))
    lines = ["| 業態 | 合計 | 個人店（名前あり） | チェーン | 名前なし | 営業時間あり（個人店） | 公式サイトあり（個人店） |", "|---|---|---|---|---|---|---|"]
    for k, (label, _) in KINDS.items():
        rs = [r for r in rows if r["kind"] == k]
        ind = [r for r in rs if not r["chain"] and r["name"]]
        lines.append(f"| {label} | {len(rs)} | {len(ind)} | {sum(1 for r in rs if r['chain'])} | {sum(1 for r in rs if not r['name'])} "
                     f"| {sum(1 for r in ind if r['hours'])} | {sum(1 for r in ind if r['web'])} |")
    cu = collections.Counter(r["cuisine"].split(";")[0] for r in rows if r["kind"] == "restaurant" and not r["chain"] and r["name"] and r["cuisine"])
    lines += ["", f"飲食店（個人店）で料理の種類が入っているもの：{sum(cu.values())}件。多い順：" + "、".join(f"{c} {n}" for c, n in cu.most_common(12))]
    lines += ["", "チェーンと判定された名前の多いもの：" + "、".join(f"{n} {c}" for n, c in names.most_common(12) if c >= 3)]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with open(out / "shops.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["業態", "種類", "名前", "チェーン", "料理", "営業時間", "公式サイト", "区", "緯度", "経度", "OSM"])
        for r in rows:
            w.writerow([KINDS[r["kind"]][0], r["tag"], r["name"], "○" if r["chain"] else "", r["cuisine"], "○" if r["hours"] else "",
                        "○" if r["web"] else "", r["area"], round(r["lat"], 6), round(r["lon"], 6), f"{r['type']}{r['id']}"])
    print("\n".join(lines))


if __name__ == "__main__":
    main()
