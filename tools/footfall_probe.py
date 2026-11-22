"""人通りの材料の調査（読み取りだけ・DBは触らない）

  python tools/footfall_probe.py [--out DIR]

やること
  1. 国土数値情報「駅別乗降客数（S12）」の全国 zip（GeoJSON）を取り、福岡市の範囲の駅の数・年度・乗降客数の埋まり具合を出す
  2. e-Stat の地図で見る統計（統計GIS）から、経済センサス（町丁・大字別）の境界・統計データのダウンロードのリンクが取れるかを調べる
外部への取得は直列・1秒以上の間隔。利用条件：S12 は CC BY 4.0（2022年度版）。e-Stat は出典の表示。
"""
import argparse
import io
import json
import re
import sys
import time
import zipfile
from pathlib import Path

import requests

UA = "fukuoka-locadb/2.0 (https://github.com/mandrill-fukuoka/fukuoka-locadb; location research)"
BBOX = (130.20, 33.43, 130.52, 33.72)
S12_URLS = [
    "https://nlftp.mlit.go.jp/ksj/gml/data/S12/S12-23/S12-23_GML.zip",
    "https://nlftp.mlit.go.jp/ksj/gml/data/S12/S12-22/S12-22_GML.zip",
]
ESTAT_PAGE = ("https://www.e-stat.go.jp/gis/statmap-search?page=1&type=2&aggregateUnitForBoundary=A"
              "&toukeiCode=00200553&toukeiYear=2021&serveyId=B002005512021")


def get(url, **kw):
    time.sleep(1.1)
    return requests.get(url, headers={"User-Agent": UA}, timeout=120, **kw)


def stations(out: dict, outdir: Path) -> None:
    for url in S12_URLS:
        r = get(url)
        out.setdefault("s12_tries", []).append({"url": url, "status": r.status_code, "bytes": len(r.content)})
        if r.status_code != 200:
            continue
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = z.namelist()
        out["s12_files"] = names[:30]
        gj = next((n for n in names if n.lower().endswith(".geojson")), None)
        if not gj:
            out["s12_error"] = "GeoJSON が入っていない"
            return
        data = json.loads(z.read(gj).decode("utf-8"))
        feats = data["features"]
        out["s12_total"] = len(feats)
        x0, y0, x1, y1 = BBOX
        local = []
        for f in feats:
            g = f["geometry"]
            if g["type"] == "Point":
                lon, lat = g["coordinates"][:2]
            else:                                   # 線（駅の形）は最初の点
                c = g["coordinates"]
                while isinstance(c[0], list):
                    c = c[0]
                lon, lat = c[:2]
            if x0 <= lon <= x1 and y0 <= lat <= y1:
                local.append((lon, lat, f["properties"]))
        out["s12_fukuoka"] = len(local)
        out["s12_geometry"] = feats[0]["geometry"]["type"]
        out["s12_property_keys"] = list(feats[0]["properties"].keys())[:80]
        out["s12_sample"] = [p for _, _, p in local[:3]]
        # 最新年度の乗降客数がある駅
        keys = [k for k in feats[0]["properties"] if k.endswith(("_043", "_044", "_045", "_046", "_047", "_048", "_049", "_050", "_051", "_052", "_053"))]
        out["s12_candidate_keys"] = keys
        (outdir / "s12_fukuoka.json").write_text(json.dumps(
            [{"lon": a, "lat": b, "p": p} for a, b, p in local], ensure_ascii=False, indent=1), encoding="utf-8")
        return


def estat(out: dict) -> None:
    r = get(ESTAT_PAGE)
    out["estat_page"] = {"status": r.status_code, "bytes": len(r.content)}
    links = sorted(set(re.findall(r"""["'](/?[^"']*statmap-search/data\?[^"']+|[^"']*file-download\?[^"']+)["']""", r.text)))
    out["estat_links"] = links[:20]
    out["estat_form_hints"] = sorted(set(re.findall(r'(?:name|id)="([A-Za-z]*(?:serve|code|coord|format|download)[A-Za-z]*)"', r.text)))[:30]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="footfall-out")
    a = ap.parse_args(argv)
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    out = {}
    for fn in (stations, estat):
        try:
            fn(out, outdir) if fn is stations else fn(out)
        except Exception as e:  # noqa: BLE001
            out[f"{fn.__name__}_error"] = f"{type(e).__name__}: {e}"
    (outdir / "summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    (outdir / "summary.md").write_text("```json\n" + json.dumps(out, ensure_ascii=False, indent=1)[:60000] + "\n```", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
