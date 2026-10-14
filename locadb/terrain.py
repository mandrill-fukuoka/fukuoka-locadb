"""地点の地形（標高・高台度・最大の傾き）を国土地理院の標高タイルから入れる（無料・キー不要）

  python -m locadb.terrain            # 未取得の地点と、取得後に動いた地点（自動収集の一工程）
  python -m locadb.terrain --all      # 全件を取り直す（計算方法を変えたとき）

- 1地点につき17点：中心、半径30mの8方向（傾き）、半径150mの8方向（高台度）
- 標高タイル（PNG）を使う。細かい順に 5m・レーザ（dem5a）→ 5m・写真測量（dem5b）→ 10m（dem10b）。
  中心に値がある最初のデータを使い、周りの16点も同じデータから読む（データの混在で段差ができないように）
- どのデータにも中心の値がない（海上など）→ terrain_status='no_data'、標高などは空
- 周りの点は海などで値がないことがある。8方向のうち MIN_RING 点以上に値があるときだけ計算する
- 標高タイルは建物を除いた地面の高さ。ビルの屋上・展望台の高さは出ない。橋の地点は橋の下の地面の高さ
- タイルの取得に失敗した地点は書かずに残し、次の回で取り直す
出典の表記：「国土地理院 標高タイル」（地点ページの出典に表示）
"""
import argparse
import io
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
from PIL import Image

from .db import connect, job

URL = "https://cyberjapandata.gsi.go.jp/xyz/{path}/{z}/{x}/{y}.png"
LAYERS = [("dem5a", "dem5a_png", 15), ("dem5b", "dem5b_png", 15), ("dem10b", "dem_png", 14)]
R_SLOPE = 30.0           # 傾きを見る半径（m）
R_RELIEF = 150.0         # 高台度を見る半径（m）
MIN_RING = 5             # 8方向のうち、これ以上の点に値があれば計算する
WORKERS = 4              # 国土地理院への同時接続数
BATCH = 500
UA = {"User-Agent": "fukuoka-locadb/2.0 (location research)"}
M_PER_DEG = 111320.0


class FetchError(Exception):
    pass


def ring(lat: float, lon: float, r: float) -> list[tuple[float, float]]:
    """(lat, lon) から半径 r m の8方向（北から時計回り）"""
    out = []
    for i in range(8):
        a = math.radians(45 * i)
        out.append((lat + r * math.cos(a) / M_PER_DEG,
                    lon + r * math.sin(a) / (M_PER_DEG * math.cos(math.radians(lat)))))
    return out


def pixel(lat: float, lon: float, z: int) -> tuple[int, int, int, int]:
    """(タイルx, タイルy, タイル内のx, タイル内のy)"""
    n = 2 ** z * 256
    px = (lon + 180.0) / 360.0 * n
    py = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    ix, iy = int(px), int(py)
    return ix // 256, iy // 256, ix % 256, iy % 256


def decode_png(data: bytes) -> np.ndarray:
    """標高PNG → 256×256 の標高（m）。無効値（128,0,0）は nan"""
    a = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.int64)
    x = (a[:, :, 0] << 16) | (a[:, :, 1] << 8) | a[:, :, 2]
    h = np.where(x < 2 ** 23, x, x - 2 ** 24).astype(np.float32) * 0.01
    h[x == 2 ** 23] = np.nan
    return h


class Tiles:
    """標高タイルの取得と読み取り（1回の実行の間だけ覚えておく）"""

    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.cache: dict[tuple, np.ndarray | None] = {}
        self.failed: set[tuple] = set()
        self.fetched = 0                          # 問い合わせたタイルの数（タイルがなかったものも含む）

    def fetch(self, path: str, z: int, x: int, y: int) -> bytes | None:
        """PNG を返す。タイルがない（404）なら None。何度やっても取れなければ FetchError"""
        for attempt in range(3):
            try:
                r = self.session.get(URL.format(path=path, z=z, x=x, y=y), timeout=30, headers=UA)
                if r.status_code == 200:
                    return r.content
                if r.status_code == 404:
                    return None
            except requests.RequestException:
                pass
            time.sleep(2 * (attempt + 1))
        raise FetchError(f"{path}/{z}/{x}/{y}")

    def _load(self, key):
        try:
            data = self.fetch(*key)
        except FetchError:
            return key, None, True
        return key, (decode_png(data) if data else None), False

    def prefetch(self, keys: set[tuple]):
        todo = [k for k in keys if k not in self.cache and k not in self.failed]
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for key, arr, failed in pool.map(self._load, todo):
                if failed:
                    self.failed.add(key)
                else:
                    self.cache[key] = arr
                    self.fetched += 1

    @staticmethod
    def key(layer, lat, lon):
        _, path, z = layer
        tx, ty, _, _ = pixel(lat, lon, z)
        return (path, z, tx, ty)

    def value(self, layer, lat, lon) -> float | None:
        """標高（m）。値がない・タイルがないときは None。取得に失敗したタイルなら FetchError"""
        k = self.key(layer, lat, lon)
        if k in self.failed:
            raise FetchError("/".join(map(str, k)))
        arr = self.cache.get(k)
        if arr is None:
            return None
        _, _, px, py = pixel(lat, lon, layer[2])
        v = float(arr[py, px])
        return None if math.isnan(v) else v


def points_of(lat, lon):
    return [(lat, lon)] + ring(lat, lon, R_SLOPE) + ring(lat, lon, R_RELIEF)


def measure(tiles: Tiles, layer, lat, lon) -> dict | None:
    """そのデータで中心に値があれば地形を返す。なければ None"""
    h0 = tiles.value(layer, lat, lon)
    if h0 is None:
        return None
    near = [tiles.value(layer, a, b) for a, b in ring(lat, lon, R_SLOPE)]
    far = [tiles.value(layer, a, b) for a, b in ring(lat, lon, R_RELIEF)]
    near = [v for v in near if v is not None]
    far = [v for v in far if v is not None]
    slope = max(abs(v - h0) for v in near) / R_SLOPE * 100 if len(near) >= MIN_RING else None
    relief = h0 - sum(far) / len(far) if len(far) >= MIN_RING else None
    return {"elevation_m": round(h0, 2), "relief_m": None if relief is None else round(relief, 1),
            "slope_pct": None if slope is None else round(slope, 1), "terrain_src": layer[0]}


def compute(spots: list[dict], tiles: Tiles) -> tuple[dict, set]:
    """spots: [{id, lat, lon}] → ({id: 結果 or 'no_data'}, 取得に失敗した地点のid)"""
    results: dict[int, dict | str] = {}
    failed: set[int] = set()
    remaining = list(spots)
    for layer in LAYERS:
        if not remaining:
            break
        tiles.prefetch({tiles.key(layer, a, b) for s in remaining for a, b in points_of(s["lat"], s["lon"])})
        nxt = []
        for s in remaining:
            try:
                r = measure(tiles, layer, s["lat"], s["lon"])
            except FetchError:
                failed.add(s["id"])
                continue
            if r is None:
                nxt.append(s)
            else:
                results[s["id"]] = r
        remaining = nxt
    for s in remaining:
        results[s["id"]] = "no_data"
    return results, failed


def targets(conn, all_: bool = False, limit: int = 20000) -> list[dict]:
    return conn.execute(
        """select s.id, ST_Y(s.geom::geometry) lat, ST_X(s.geom::geometry) lon
             from spots s
            where s.data_status <> 'archived'
              and (%s or s.terrain_at is null
                   or s.terrain_at < (select max(h.at) from spot_history h where h.spot_id = s.id and h.event = 'moved'))
            order by s.id limit %s""", (all_, limit)).fetchall()


def distribution(conn) -> dict:
    """取得済みの地点の分布（高台・坂道の目安を決める材料）"""
    r = conn.execute(
        """select count(*) n,
                  percentile_cont(array[0.1,0.5,0.9,0.99]) within group (order by elevation_m) elev,
                  percentile_cont(array[0.5,0.9,0.95,0.99]) within group (order by relief_m) relief,
                  percentile_cont(array[0.5,0.9,0.95,0.99]) within group (order by slope_pct) slope
             from spots where data_status <> 'archived' and terrain_status = 'ok'""").fetchone()
    rd = lambda xs: [None if x is None else round(x, 1) for x in (xs or [])]  # noqa: E731
    return {"ok": r["n"], "elevation_p10_50_90_99": rd(r["elev"]), "relief_p50_90_95_99": rd(r["relief"]),
            "slope_p50_90_95_99": rd(r["slope"])}


def run(run_id: int | None = None, all_: bool = False, limit: int = 20000, tiles: Tiles | None = None) -> dict:
    tiles = tiles or Tiles()
    with connect() as conn, job(conn, "terrain", run_id) as stats:
        rows = targets(conn, all_, limit)
        stats.update(target=len(rows), ok=0, no_data=0, failed=0)
        for i in range(0, len(rows), BATCH):
            chunk = [dict(r) for r in rows[i:i + BATCH]]
            res, failed = compute(chunk, tiles)
            batch = []
            for sid, r in res.items():
                if r == "no_data":
                    batch.append((None, None, None, None, "no_data", sid))
                    stats["no_data"] += 1
                else:
                    batch.append((r["elevation_m"], r["relief_m"], r["slope_pct"], r["terrain_src"], "ok", sid))
                    stats["ok"] += 1
            stats["failed"] += len(failed)
            if batch:
                with conn.cursor() as cur:
                    cur.executemany("""update spots set elevation_m=%s, relief_m=%s, slope_pct=%s, terrain_src=%s,
                                              terrain_status=%s, terrain_at=now() where id=%s""", batch)
                conn.commit()
            print(f"    地形 {min(i + BATCH, len(rows))}/{len(rows)}（タイル {tiles.fetched}）", flush=True)
        stats["tiles"] = tiles.fetched
        stats["tiles_failed"] = len(tiles.failed)
        stats["distribution"] = distribution(conn)
        print(f"    地形を入れた地点 {stats['ok']}（データなし {stats['no_data']}・取得できず {stats['failed']}）")
        print(f"    分布 {stats['distribution']}")
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="全件を取り直す")
    ap.add_argument("--limit", type=int, default=20000)
    a = ap.parse_args()
    st = run(all_=a.all, limit=a.limit)
    return 1 if st["failed"] and st["failed"] >= max(10, st["target"] // 10) else 0


if __name__ == "__main__":
    sys.exit(main())
