"""PLATEAU（福岡市の3D都市モデル）の建築物データに、高さ・階数・用途の属性が入っているかを調べる（読み取りだけ・DBは触らない）

  python tools/plateau_probe.py [--dataset plateau-40130-fukuoka-shi-2024] [--files 8] [--out DIR]

やること
  1. G空間情報センター（CKAN）の API から、データセットのファイル一覧（名前・形式・大きさ）を取る
  2. CityGML の zip から、建築物のファイル（udx/bldg/*.gml）を HTTP の範囲指定で数ファイルだけ取り出す（zip 全体は落とさない）
  3. 建物ごとに、高さ・階数・用途・名称・建築年などの属性があるかを数える
外部への取得は直列・1秒以上の間隔。利用条件：PLATEAU サイトポリシー（商用利用可・出典の表示）。
"""
import argparse
import collections
import json
import re
import sys
import time
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

CKAN = "https://www.geospatial.jp/ckan/api/3/action/package_show"
UA = "fukuoka-locadb/2.0 (https://github.com/mandrillfuk-dev/fukuoka-locadb; location research)"
INTERVAL_S = 1.1
FIELDS = {                       # 属性名（タグの末尾）→ 意味
    "measuredHeight": "高さ", "storeysAboveGround": "地上階数", "storeysBelowGround": "地下階数",
    "usage": "用途", "yearOfConstruction": "建築年", "class": "分類", "name": "名称",
    "buildingStructureType": "構造", "detailedUsage": "詳細用途", "buildingRoofEdgeArea": "屋根面積",
}


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def analyze(xml_bytes: bytes, samples: list) -> collections.Counter:
    c = collections.Counter()
    root = ET.fromstring(xml_bytes)
    for b in root.iter():
        if local(b.tag) != "Building":
            continue
        c["buildings"] += 1
        seen = {}
        for e in b.iter():
            k = local(e.tag)
            if k in FIELDS and (e.text or "").strip():
                seen.setdefault(k, e.text.strip())
        for k in seen:
            c[k] += 1
        if len(samples) < 12 and ("measuredHeight" in seen or "usage" in seen or "name" in seen):
            samples.append(seen)
    return c


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="plateau-40130-fukuoka-shi-2024")
    ap.add_argument("--files", type=int, default=8)
    ap.add_argument("--out", default="plateau-out")
    a = ap.parse_args(argv)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    s = requests.Session(); s.headers["User-Agent"] = UA
    pkg = s.get(CKAN, params={"id": a.dataset}, timeout=60).json()["result"]
    res = [{"name": r.get("name"), "format": r.get("format"), "size": r.get("size"), "url": r.get("url")} for r in pkg["resources"]]
    (out / "resources.json").write_text(json.dumps({"license": pkg.get("license_title"), "notes": (pkg.get("notes") or "")[:1500],
                                                    "resources": res}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"resources: {len(res)}  license: {pkg.get('license_title')}")
    for r in res:
        print(" -", r["format"], r["size"], r["name"], r["url"])
    zips = [r for r in res if (r["url"] or "").lower().endswith(".zip") and "citygml" in (r["url"] or "").lower()]
    if not zips:
        print("CityGML の zip が見つかりません", file=sys.stderr)
        return 1
    from remotezip import RemoteZip
    total, samples, used = collections.Counter(), [], []
    for z in zips[:1]:
        with RemoteZip(z["url"], headers={"User-Agent": UA}) as rz:
            names = [n for n in rz.namelist() if "/bldg/" in n and n.lower().endswith(".gml")]
            print(f"zip: {z['url']}  bldg ファイル数: {len(names)}")
            step = max(1, len(names) // a.files)
            for n in names[::step][: a.files]:
                time.sleep(INTERVAL_S)
                data = rz.read(n)
                c = analyze(data, samples)
                total.update(c); used.append({"file": n, "bytes": len(data), **c})
                print(f"  {n}: {len(data)} bytes, 建物 {c['buildings']}")
    n = total["buildings"] or 1
    lines = ["| 属性 | 入っている建物 | 割合 |", "|---|---:|---:|"]
    for k, label in FIELDS.items():
        lines.append(f"| {label}（{k}） | {total[k]} | {total[k] * 100 // n}% |")
    summary = f"建物 {total['buildings']} 棟（{len(used)} ファイルの抜き取り）\n\n" + "\n".join(lines)
    (out / "summary.md").write_text(summary, encoding="utf-8")
    (out / "samples.json").write_text(json.dumps(samples, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "files.json").write_text(json.dumps(used, ensure_ascii=False, indent=1), encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
