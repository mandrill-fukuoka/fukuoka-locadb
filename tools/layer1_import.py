"""層1の外部データの取り込み（二度実行しても同じ結果）

  python tools/layer1_import.py did  [--dry-run]    国土数値情報 人口集中地区 A16（平成27年・福岡県）→ did_areas
  python tools/layer1_import.py tide [--dry-run] [--years 2026,2027]   気象庁 潮位表 博多（QF）→ tide_daily
利用条件：国土数値情報の人口集中地区は商用可（出典の表示と加工の明示）。気象庁は出典「気象庁ホームページ」と加工の明示。
外部への取得は直列・1秒以上の間隔。
"""
import argparse
import io
import json
import sys
import time
import zipfile
from pathlib import Path

import requests

UA = "fukuoka-locadb/2.0 (https://github.com/mandrillfuk-dev/fukuoka-locadb; location research)"
DID_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/A16/A16-15/A16-15_40_GML.zip"
JMA_URL = "https://www.data.jma.go.jp/gmd/kaiyou/data/db/tide/suisan/txt/{year}/QF.txt"


def get(url: str) -> bytes:
    time.sleep(1.1)
    r = requests.get(url, timeout=180, headers={"User-Agent": UA})
    r.raise_for_status()
    return r.content


def _hm(s: str):
    """' 829' → '08:29'。欠測（9999）は None"""
    s = s.strip()
    if not s or s == "9999":
        return None
    s = s.rjust(4, "0")
    return f"{s[:2]}:{s[2:]}"


def parse_tide_line(line: str):
    """気象庁の潮位表（毎時潮位 24×3桁・年月日・地点・満潮4×(時刻4桁+潮位3桁)・干潮4×(時刻4桁+潮位3桁)）の1行 → (日付, 満潮, 干潮)"""
    line = line.rstrip("\n")
    if len(line) < 136:
        return None
    yy, mm, dd = int(line[72:74]), int(line[74:76]), int(line[76:78])
    day = f"{2000 + yy:04d}-{mm:02d}-{dd:02d}"

    def block(start):
        out = []
        for k in range(4):
            seg = line[start + 7 * k: start + 7 * k + 7]
            t = _hm(seg[:4])
            cm = seg[4:].strip()
            if t and cm and cm != "999":
                out.append({"t": t, "cm": int(cm)})
        return out
    return day, block(80), block(108)


def tide(years, dry):
    rows = []
    for y in years:
        text = get(JMA_URL.format(year=y)).decode("utf-8", "replace")
        for ln in text.splitlines():
            p = parse_tide_line(ln)
            if p and ln[78:80] == "QF":
                rows.append(p)
    print(json.dumps({"tide_days": len(rows), "first": rows[0] if rows else None, "last": rows[-1] if rows else None}, ensure_ascii=False))
    if dry or not rows:
        return
    from locadb.db import connect
    with connect() as conn:
        with conn.cursor() as cur:
            cur.executemany("""insert into tide_daily (day, station, highs, lows) values (%s,'QF',%s::jsonb,%s::jsonb)
                               on conflict (day) do update set highs = excluded.highs, lows = excluded.lows, fetched_at = now()""",
                            [(d, json.dumps(h), json.dumps(l)) for d, h, l in rows])
        conn.commit()


def did(dry):
    import shapefile
    from shapely.geometry import MultiPolygon, shape
    z = zipfile.ZipFile(io.BytesIO(get(DID_URL)))
    base = [n for n in z.namelist() if n.lower().endswith(".shp")][0][:-4]
    sf = shapefile.Reader(shp=io.BytesIO(z.read(base + ".shp")), dbf=io.BytesIO(z.read(base + ".dbf")),
                          shx=io.BytesIO(z.read(base + ".shx")), encoding="cp932")
    names = [f[0] for f in sf.fields[1:]]
    out = []
    for sr in sf.iterShapeRecords():
        rec = dict(zip(names, sr.record))
        g = shape(sr.shape.__geo_interface__)
        if g.is_empty:
            continue
        if not g.is_valid:
            g = g.buffer(0)
        if g.geom_type == "Polygon":
            g = MultiPolygon([g])
        out.append({"name": str(rec.get("A16_002") or ""), "pop": int(rec.get("A16_005") or 0) or None, "wkt": g.wkt})
    print(json.dumps({"did_areas": len(out), "sample": [{"name": o["name"], "pop": o["pop"]} for o in out[:3]]}, ensure_ascii=False))
    if dry or not out:
        return
    from locadb.db import connect
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from did_areas")
            cur.executemany("insert into did_areas (name, pop, geom) values (%s, %s, ST_Multi(ST_GeomFromText(%s, 4326))::geography)",
                            [(o["name"], o["pop"], o["wkt"]) for o in out])
        conn.commit()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["did", "tide"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--years", default="2026,2027")
    a = ap.parse_args(argv)
    if a.what == "did":
        did(a.dry_run)
    else:
        tide([int(x) for x in a.years.split(",")], a.dry_run)


if __name__ == "__main__":
    main()
