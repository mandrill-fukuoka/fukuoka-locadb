"""駅の人の多さ：国土数値情報「駅別乗降客数データ（S12）」から福岡市の範囲の駅の乗降客数を取り込む（DB変更293。AIなし・費用ゼロ）

  python -m locadb.station_flow --dry-run [--url URL] [--out DIR]   読み取りのみ。駅の数と上位を出す
  python -m locadb.station_flow --apply   [--url URL]               station_flow を作り直し、footfall_refresh と vibe_apply を流す

- データ：国土交通省 国土数値情報 駅別乗降客数データ（CC BY 4.0。2022年度版以降）。全国の zip（約6MB）から福岡市の範囲だけを使う。
- 乗降客数は事業者ごとの算出で、統一基準ではない。「重複コード」が1以外の行（同じ駅の別路線で数が重なる行）は数えない。
  同じ駅のグループ（300m以内の同名駅）は事業者ごとの数を足す。最新年度（2022）の値が空なら前の年度を使う。
- 年に1回ほどの更新なので、月次の取り込みには入れていない（手動の station-import ワークフロー）。
- 画面の出典：国土数値情報（駅別乗降客数データ）（NOTICE.md に記載）。
"""
import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

from .db import connect, job

BBOX = (130.20, 33.43, 130.52, 33.72)           # lon_min, lat_min, lon_max, lat_max（福岡市7区を含む箱）
URLS = ["https://nlftp.mlit.go.jp/ksj/gml/data/S12/S12-24/S12-24_GML.zip",
        "https://nlftp.mlit.go.jp/ksj/gml/data/S12/S12-23/S12-23_GML.zip"]      # 新しい版が出たらここに足す（上から順に試す）
MIN_STATIONS = 60                                # これ未満なら取得の失敗とみる（2022年度版で福岡市の範囲に101駅）
# 年度ごとの4項目（重複コード・データ有無・備考・乗降客数）が2011年度から順に並ぶ（S12_006〜）。最新の年度は列の数から決める：
#   2022年度版（S12_053まで）→ 2022、次の版（S12_057まで）→ 2023。年度 y の乗降客数は S12_(9+4*(y-2011))、重複コードは S12_(6+4*(y-2011))
FIRST_YEAR, MIN_YEAR = 2011, 2019                # 最新から 2019（コロナ前）まで、値のある最新の年度を使う


def year_keys(props: dict) -> list[tuple[int, str, str]]:
    n = max((int(k[4:]) for k in props if k.startswith("S12_") and k[4:].isdigit()), default=0)
    latest = FIRST_YEAR + (n - 5) // 4 - 1
    return [(y, f"S12_{9 + 4 * (y - FIRST_YEAR):03d}", f"S12_{6 + 4 * (y - FIRST_YEAR):03d}")
            for y in range(latest, MIN_YEAR - 1, -1)]


def fetch_zip(url: str | None = None) -> tuple[bytes, str]:
    import requests
    last = ""
    for u in ([url] if url else URLS):
        r = requests.get(u, headers={"User-Agent": "fukuoka-locadb/2.0"}, timeout=180)
        if r.status_code == 200 and r.content[:2] == b"PK":
            return r.content, u
        last = f"{u} → {r.status_code}"
    raise SystemExit(f"駅別乗降客数の zip を取れませんでした（{last}）")


def parse(zip_bytes: bytes) -> list[dict]:
    """福岡市の範囲の駅（グループごと）：{group_code, name, operators, pax, fiscal_year, lon, lat}"""
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    gj = next((n for n in z.namelist() if n.lower().endswith(".geojson") and n.startswith("UTF-8/")), None)
    if not gj:
        raise SystemExit("GeoJSON（UTF-8）が zip に入っていません")
    feats = json.loads(z.read(gj).decode("utf-8"))["features"]
    return group(feats)


def point_of(geom: dict) -> tuple[float, float]:
    """駅の形（線）の代表点：頂点の平均"""
    c = geom["coordinates"]
    if geom["type"] == "Point":
        return float(c[0]), float(c[1])
    pts = []
    def walk(x):
        if x and isinstance(x[0], (int, float)):
            pts.append(x)
        else:
            for y in x:
                walk(y)
    walk(c)
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def group(feats: list[dict]) -> list[dict]:
    x0, y0, x1, y1 = BBOX
    groups: dict[str, dict] = {}
    for f in feats:
        p = f["properties"]
        lon, lat = point_of(f["geometry"])
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            continue
        for year, pax_key, dup_key in year_keys(p):
            pax = p.get(pax_key)
            if pax is not None and pax > 0 and p.get(dup_key) == 1:        # 重複コード 1 だけ数える
                break
        else:
            continue
        g = groups.setdefault(p["S12_001g"], {"group_code": p["S12_001g"], "name": p["S12_001"], "operators": set(),
                                              "pax": 0, "years": [], "pts": []})
        g["operators"].add(p["S12_002"])
        g["pax"] += int(pax)
        g["years"].append(year)
        g["pts"].append((lon, lat))
    out = []
    for g in groups.values():
        out.append({"group_code": g["group_code"], "name": g["name"], "operators": sorted(g["operators"]), "pax": g["pax"],
                    "fiscal_year": min(g["years"]),
                    "lon": sum(a for a, _ in g["pts"]) / len(g["pts"]), "lat": sum(b for _, b in g["pts"]) / len(g["pts"])})
    return sorted(out, key=lambda r: -r["pax"])


def replace_stations(conn, rows: list[dict], source: str) -> int:
    with conn.transaction():
        conn.execute("delete from station_flow")
        for r in rows:
            conn.execute("insert into station_flow (group_code, name, operators, pax, fiscal_year, geom, source) "
                         "values (%s,%s,%s,%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, %s)",
                         (r["group_code"], r["name"], r["operators"], r["pax"], r["fiscal_year"], r["lon"], r["lat"], source))
    return len(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--url", default="")
    ap.add_argument("--out", default="station-out")
    a = ap.parse_args(argv)
    data, url = fetch_zip(a.url or None)
    rows = parse(data)
    source = url.rsplit("/", 1)[-1].replace("_GML.zip", "")
    stats = {"dry_run": a.dry_run, "source": source, "stations": len(rows),
             "years": {str(y): sum(1 for r in rows if r["fiscal_year"] == y) for y in sorted({r["fiscal_year"] for r in rows})}, "pax_total": sum(r["pax"] for r in rows),
             "top": [(r["name"], r["pax"]) for r in rows[:8]]}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    with connect() as conn, job(conn, "station_flow") as st:
        before = conn.execute("select count(*) n from station_flow").fetchone()["n"]
        stats["before"] = before
        if len(rows) < MIN_STATIONS:
            raise SystemExit(f"取り込む駅の数（{len(rows)}）が少なすぎるので止めました（取得の失敗かも。下限 {MIN_STATIONS}）")
        if a.apply and before and len(rows) < before * 0.7:
            raise SystemExit(f"取り込む駅の数（{len(rows)}）が今（{before}）の7割未満なので止めました")
        if a.apply:
            replace_stations(conn, rows, source)
            stats["spots_refreshed"] = conn.execute("select footfall_refresh() n").fetchone()["n"]
            stats["vibe"] = {k: v for k, v in conn.execute("select vibe_apply() v").fetchone()["v"].items()
                             if k in ("updated", "version")}
            stats.update({k: v for k, v in conn.execute("select footfall_stats() d").fetchone()["d"].items()
                          if k.endswith("p10_50_90_99") or k.startswith("spots_with")})
        st.update(stats)
    (Path(a.out) / "station.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
