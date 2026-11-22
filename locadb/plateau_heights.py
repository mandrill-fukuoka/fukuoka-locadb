"""PLATEAU（福岡市の3D都市モデル）の建築物の高さから、地点ごとに周りの建物の高さの目安を作る（DB変更291。AIなし・費用ゼロ）

  python -m locadb.plateau_heights --dry-run [--files N] [--out DIR]   読み取りのみ（DBは地点の位置を読むだけ）
  python -m locadb.plateau_heights --apply                              spot_skyline に入れ、vibe_apply を流す

- データ：G空間情報センターの「plateau-40130-fukuoka-shi-2024」の CityGML の zip から、建築物（udx/bldg/*.gml）を
  HTTP の範囲指定で1ファイルずつ取り出す（zip 全体は落とさない。直列・1秒以上の間隔）。利用条件：PLATEAU サイトポリシー（商用可・出典表示）。
- 建物ごとに、実測の高さ（measuredHeight）と、形状の頂点の平均（位置の目安）だけを読む。建物そのものは保存しない。
- 地点ごと：半径100m内の高さの最大・平均、半径300m内の高さ45m以上の建物の数、地上1.6mから16方位に見た見上げ角の平均・最大。
- 建物が極端に少ない（取得の失敗かも）ときは止まる。半径300m内に建物がない地点は行を作らない（＝値なしで、点数に使わない）。
"""
import argparse
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import requests

from .db import connect, job

CKAN = "https://www.geospatial.jp/ckan/api/3/action/package_show"
DATASET = "plateau-40130-fukuoka-shi-2024"
UA = "fukuoka-locadb/2.0 (https://github.com/mandrill-fukuoka/fukuoka-locadb; location research)"
INTERVAL_S = 1.1
MIN_BUILDINGS = 300_000                    # 2024年版の福岡市は約52万棟（8ファイル抜き取りの10,966棟×383ファイルからの見積り）
TALL_M = 45.0
EYE_M = 1.6
SECTORS = 16
LAT0 = 33.6
M_LAT = 110_574.0
M_LON = 111_320.0 * math.cos(math.radians(LAT0))


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_buildings(xml_bytes: bytes) -> list[tuple[float, float, float]]:
    """(lat, lon, 高さm) の一覧。高さがない・0以下・400m超の建物は捨てる。座標の順は値の大きさで見分ける（緯度が先なら30台）"""
    out = []
    for _, b in ET.iterparse(__import__("io").BytesIO(xml_bytes), events=("end",)):
        if local(b.tag) != "Building":
            continue
        h, la, lo, n = None, 0.0, 0.0, 0
        for e in b.iter():
            k = local(e.tag)
            if k == "measuredHeight" and (e.text or "").strip() and h is None:
                try:
                    h = float(e.text)
                except ValueError:
                    h = None
            elif k == "posList" and (e.text or "").strip():
                v = e.text.split()
                dim = 3 if len(v) % 3 == 0 else 2
                for i in range(0, len(v) - dim + 1, dim):
                    a, c = float(v[i]), float(v[i + 1])
                    lat, lon = (a, c) if a < 90 else (c, a)
                    la += lat; lo += lon; n += 1
        b.clear()
        if h is not None and 0 < h <= 400 and n:
            out.append((la / n, lo / n, h))
    return out


def to_xy(lat, lon):
    return (np.asarray(lon) - 130.4) * M_LON, (np.asarray(lat) - LAT0) * M_LAT


class Grid:
    """100mのマス目で建物を引く。半径300mまでの近傍を取る"""
    CELL = 100.0

    def __init__(self, x, y, h):
        self.x, self.y, self.h = x, y, h
        cx, cy = np.floor(x / self.CELL).astype(np.int64), np.floor(y / self.CELL).astype(np.int64)
        key = cx * 1_000_003 + cy
        order = np.argsort(key, kind="stable")
        self.order, ks = order, key[order]
        uniq, start = np.unique(ks, return_index=True)
        end = np.append(start[1:], len(ks))
        self.cells = {int(k): (int(s), int(e)) for k, s, e in zip(uniq, start, end)}

    def near(self, px, py, r=300.0):
        c0x, c0y = int(math.floor(px / self.CELL)), int(math.floor(py / self.CELL))
        n = int(math.ceil(r / self.CELL))
        idx = []
        for i in range(c0x - n, c0x + n + 1):
            for j in range(c0y - n, c0y + n + 1):
                se = self.cells.get(i * 1_000_003 + j)
                if se:
                    idx.append(self.order[se[0]:se[1]])
        if not idx:
            return np.empty(0, dtype=np.int64), np.empty(0), np.empty(0)
        ii = np.concatenate(idx)
        dx, dy = self.x[ii] - px, self.y[ii] - py
        d = np.hypot(dx, dy)
        m = d <= r
        return ii[m], dx[m], dy[m]


def metrics(grid: Grid, lat: float, lon: float) -> dict | None:
    px, py = (lon - 130.4) * M_LON, (lat - LAT0) * M_LAT
    ii, dx, dy = grid.near(px, py, 300.0)
    if len(ii) == 0:
        return None
    d, h = np.hypot(dx, dy), grid.h[ii]
    near100 = d <= 100.0
    r = {"h_max_100": float(h[near100].max()) if near100.any() else None,
         "h_mean_100": float(h[near100].mean()) if near100.any() else None,
         "tall_300": int((h >= TALL_M).sum())}
    far = d >= 5.0                                  # 自分自身の建物は見上げ角に入れない
    best = np.zeros(SECTORS)
    if far.any():
        ang = np.degrees(np.arctan2(np.maximum(h[far] - EYE_M, 0.0), d[far]))
        sec = (np.floor(((np.arctan2(dx[far], dy[far]) % (2 * math.pi)) / (2 * math.pi)) * SECTORS).astype(int)) % SECTORS
        np.maximum.at(best, sec, ang)
    r["horizon_mean_deg"], r["horizon_max_deg"] = float(best.mean()), float(best.max())
    return r


def fetch_buildings(max_files: int = 0, sleep=time.sleep, log=print) -> tuple[list[tuple], dict]:
    s = requests.Session(); s.headers["User-Agent"] = UA
    pkg = s.get(CKAN, params={"id": DATASET}, timeout=60).json()["result"]
    zips = [r for r in pkg["resources"] if (r.get("url") or "").lower().endswith(".zip") and "citygml" in (r.get("url") or "").lower()]
    if not zips:
        raise SystemExit("CityGML の zip が見つかりません")
    from remotezip import RemoteZip
    rows, files, bad = [], 0, 0
    with RemoteZip(zips[0]["url"], headers={"User-Agent": UA}) as rz:
        names = [n for n in rz.namelist() if "/bldg/" in n and n.lower().endswith(".gml")]
        if max_files:
            names = names[::max(1, len(names) // max_files)][:max_files]
        log(f"建築物ファイル {len(names)} 件（{zips[0]['url']}）")
        for i, n in enumerate(names, 1):
            sleep(INTERVAL_S)
            try:
                got = parse_buildings(rz.read(n))
            except Exception as e:  # noqa: BLE001
                bad += 1; log(f"  読めないファイル {n}: {type(e).__name__}"); continue
            rows += got; files += 1
            if i % 25 == 0:
                log(f"  {i}/{len(names)} ファイル、累計 {len(rows):,} 棟")
    return rows, {"files": files, "files_failed": bad, "license": pkg.get("license_title")}


UPSERT = """insert into spot_skyline (spot_id, h_max_100, h_mean_100, tall_300, horizon_mean_deg, horizon_max_deg, source, fetched_at)
            values (%(spot_id)s, %(h_max_100)s, %(h_mean_100)s, %(tall_300)s, %(horizon_mean_deg)s, %(horizon_max_deg)s, %(source)s, now())
            on conflict (spot_id) do update set h_max_100 = excluded.h_max_100, h_mean_100 = excluded.h_mean_100, tall_300 = excluded.tall_300,
                   horizon_mean_deg = excluded.horizon_mean_deg, horizon_max_deg = excluded.horizon_max_deg,
                   source = excluded.source, fetched_at = excluded.fetched_at"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--files", type=int, default=0, help="試し用：抜き取るファイル数（0＝全部）")
    ap.add_argument("--out", default="plateau-out")
    a = ap.parse_args(argv)
    rows, info = fetch_buildings(a.files)
    stats = {"dry_run": a.dry_run, "buildings": len(rows), **info, "sample_only": bool(a.files)}
    if len(rows) < (MIN_BUILDINGS if not a.files else 1000):
        raise SystemExit(f"読めた建物（{len(rows)} 棟）が少なすぎるので止めました（取得の失敗かも）")
    arr = np.array(rows)
    x, y = to_xy(arr[:, 0], arr[:, 1])
    grid = Grid(x, y, arr[:, 2])
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    with connect() as conn, job(conn, "plateau_heights") as st:
        spots = conn.execute("select s.id, ST_Y(s.geom::geometry) lat, ST_X(s.geom::geometry) lon from spots s "
                             "where s.data_status <> 'archived'").fetchall()
        res = []
        for sp in spots:
            m = metrics(grid, sp["lat"], sp["lon"])
            if m:
                res.append({"spot_id": sp["id"], **m, "source": DATASET})
        stats.update(spots=len(spots), with_values=len(res))
        for k in ("tall_300", "horizon_mean_deg", "h_max_100"):
            v = np.array([r[k] for r in res if r[k] is not None])
            if len(v):
                stats[f"{k}_p10_50_90_99"] = [round(float(q), 1) for q in np.percentile(v, [10, 50, 90, 99])]
        if a.apply:
            with conn.transaction():
                conn.execute("delete from spot_skyline")
                conn.cursor().executemany(UPSERT, res)
            stats["vibe"] = {k: v for k, v in conn.execute("select vibe_apply() v").fetchone()["v"].items() if k in ("updated", "version")}
        st.update(stats)
    (out / "plateau.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
