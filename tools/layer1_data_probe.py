"""層1の外部データ（気象庁の潮位・国土数値情報の人口集中地区）の入手先と形式を確かめる（読み取りだけ。DBは触らない）

  python tools/layer1_data_probe.py [--out DIR]
外部への取得は直列・1秒以上の間隔。結果は summary.md と生データの先頭（head-*.txt）に出す。
"""
import argparse
import io
import time
import zipfile
from pathlib import Path

import requests

UA = "fukuoka-locadb/2.0 (https://github.com/mandrillfuk-dev/fukuoka-locadb; location research)"
JMA = "https://www.data.jma.go.jp/gmd/kaiyou/data/db/tide/suisan/txt/{year}/{stn}.txt"
DID = ["https://nlftp.mlit.go.jp/ksj/gml/data/A16/A16-15/A16-15_40_GML.zip",
       "https://nlftp.mlit.go.jp/ksj/gml/data/A16/A16-15/A16-15_40_SHP.zip"]


def get(url):
    time.sleep(1.1)
    r = requests.get(url, timeout=120, headers={"User-Agent": UA})
    return r


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="layer1-out"); a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    lines = []
    for year in (2026, 2027, 2025):
        url = JMA.format(year=year, stn="QF")
        r = get(url)
        lines.append(f"- JMA {url} → {r.status_code} {len(r.content)}B {r.headers.get('content-type')}")
        if r.status_code == 200:
            txt = r.content.decode("utf-8", "replace")
            (out / f"head-QF-{year}.txt").write_text("\n".join(txt.splitlines()[:6]) + "\n", encoding="utf-8")
            lines.append(f"  行数 {len(txt.splitlines())}。先頭：`{txt.splitlines()[0][:150] if txt else ''}`")
    for url in DID:
        r = get(url)
        lines.append(f"- DID {url} → {r.status_code} {len(r.content)}B")
        if r.status_code == 200:
            z = zipfile.ZipFile(io.BytesIO(r.content))
            lines.append("  中身：" + ", ".join(f"{i.filename}({i.file_size})" for i in z.infolist()[:12]))
            for n in z.namelist():
                if n.lower().endswith(".prj"):
                    lines.append("  prj：" + z.read(n).decode("utf-8", "replace")[:200])
                if n.lower().endswith(".dbf"):
                    import shapefile
                    sf = shapefile.Reader(dbf=io.BytesIO(z.read(n)), encoding="cp932")
                    lines.append(f"  dbf {n}：{len(sf)}件 項目 {[f[0] for f in sf.fields[1:]]} 先頭 {list(sf.records()[0]) if len(sf) else ''}")
            break
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
