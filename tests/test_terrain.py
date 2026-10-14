"""回帰テスト：地形（タスクF。locadb/terrain.py）

国土地理院の標高タイルの代わりに、架空の地形のPNGタイルを作って読ませる。
  - 円錐の山：中心 (33.590, 130.390)、半径400m、高さ100m（裾の標高5m）→ 勾配25%
  - 海：緯度 33.620 より北は、どのデータにも値がない
  - 5m・レーザ（dem5a）は経度 130.4297 より東のタイルがない（404）→ 10m（dem10b）を使う
  - 5m・写真測量（dem5b）はどこにもない
"""
import io
import math

import numpy as np
import pytest
from PIL import Image

from locadb import terrain
from osmworld import World, spot

PEAK = (33.590, 130.390)
SEA_LAT = 33.620
DEM5A_MAX_X = 28256          # z15 でこのタイル番号から東は dem5a がない


def height(lat, lon):
    d = np.hypot((lat - PEAK[0]) * terrain.M_PER_DEG,
                 (lon - PEAK[1]) * terrain.M_PER_DEG * math.cos(math.radians(PEAK[0])))
    return 5.0 + np.maximum(0.0, 100.0 * (1.0 - d / 400.0))


def encode(h: np.ndarray, na: np.ndarray) -> bytes:
    x = np.round(h * 100).astype(np.int64)
    x = np.where(x < 0, x + 2 ** 24, x)
    x = np.where(na, 2 ** 23, x)
    rgb = np.stack([(x >> 16) & 255, (x >> 8) & 255, x & 255], axis=-1).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, format="PNG")
    return buf.getvalue()


class FakeTiles(terrain.Tiles):
    def __init__(self, broken=()):
        super().__init__(session=object())
        self.calls = []
        self.broken = set(broken)

    def fetch(self, path, z, x, y):
        self.calls.append((path, z, x, y))
        if (path, z, x, y) in self.broken:
            raise terrain.FetchError("broken")
        if path == "dem5b_png" or (path == "dem5a_png" and x >= DEM5A_MAX_X):
            return None
        n = 2 ** z * 256
        gx = x * 256 + np.arange(256) + 0.5
        gy = y * 256 + np.arange(256) + 0.5
        lon = gx / n * 360.0 - 180.0
        lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * gy / n))))
        LON, LAT = np.meshgrid(lon, lat)
        return encode(height(LAT, LON), LAT >= SEA_LAT)


def add_spot(db, name, lat, lon, **cols):
    extra = "".join(f", {k}" for k in cols)
    vals = "".join(", %s" for _ in cols)
    r = db.execute(f"""insert into spots (code, name, geom, category{extra})
                       values (%s, %s, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, 'park'{vals}) returning id""",
                   ("T-" + name, name, lon, lat, *cols.values())).fetchone()
    db.commit()
    return r["id"]


@pytest.fixture
def world(db, tmp_path):
    w = World()
    w.point(10, 130.385, 33.585, amenity="place_of_worship", name="寺A")     # 種別・区のマスタを入れるため
    w.run(db, tmp_path)
    return w


def test_decode_png_roundtrip():
    h = np.array([[0.0, 12.34], [-3.5, 999.99]] * 128, dtype=np.float32).repeat(128, axis=1)
    na = np.zeros_like(h, dtype=bool); na[0, 0] = True
    got = terrain.decode_png(encode(h, na))
    assert np.isnan(got[0, 0]) and got[0, 128] == pytest.approx(12.34, abs=1e-4)
    assert got[1, 0] == pytest.approx(-3.5, abs=1e-4) and got[1, 128] == pytest.approx(999.99, abs=1e-3)


def test_peak_flat_sea_coast_and_fallback(db, world):
    ids = {
        "peak": add_spot(db, "山頂", *PEAK),
        "flank": add_spot(db, "中腹", PEAK[0], PEAK[1] + 200 / (terrain.M_PER_DEG * math.cos(math.radians(PEAK[0])))),
        "flat": add_spot(db, "平地", 33.590, 130.405),
        "east": add_spot(db, "東の平地", 33.590, 130.440),
        "sea": add_spot(db, "海上", 33.630, 130.400),
        "coast": add_spot(db, "海辺", 33.619, 130.400),
    }
    st = terrain.run(tiles=FakeTiles())
    assert st["ok"] == 6 and st["no_data"] == 1 and st["failed"] == 0      # 6 = 5地点 ＋ 寺A
    g = {k: spot(db, id=v) for k, v in ids.items()}
    p = g["peak"]
    assert p["terrain_status"] == "ok" and p["terrain_src"] == "dem5a"
    assert p["elevation_m"] == pytest.approx(105, abs=1.0)
    assert p["relief_m"] == pytest.approx(37.5, abs=1.5)            # 105 − 67.5
    assert p["slope_pct"] == pytest.approx(25, abs=2)
    assert g["flank"]["slope_pct"] == pytest.approx(25, abs=2) and g["flank"]["relief_m"] < p["relief_m"]
    assert g["flat"]["relief_m"] == pytest.approx(0, abs=0.1) and g["flat"]["slope_pct"] == pytest.approx(0, abs=0.1)
    assert g["east"]["terrain_src"] == "dem10b" and g["east"]["elevation_m"] == pytest.approx(5, abs=0.1)
    s = g["sea"]
    assert s["terrain_status"] == "no_data" and s["elevation_m"] is None and s["terrain_at"] is not None
    c = g["coast"]                                                      # 北の3方向が海でも、5方向あれば計算する
    assert c["terrain_status"] == "ok" and c["relief_m"] == pytest.approx(0, abs=0.1)


def test_only_new_and_moved_spots_are_measured_again(db, world):
    a = add_spot(db, "平地A", 33.590, 130.405)
    b = add_spot(db, "平地B", 33.592, 130.405)
    terrain.run(tiles=FakeTiles())
    assert terrain.run(tiles=FakeTiles())["target"] == 0                # 2回目は何もしない
    db.execute("select edit_spot(%s, p_lat => %s, p_lon => %s, p_user => 'tester')", (b, PEAK[0], PEAK[1]))
    db.commit()
    c = db.execute("select (create_manual_spot('手入力の丘', %s, %s, 'hill', p_user => 'tester') ->> 'id')::bigint id",
                   (PEAK[0] + 0.0005, PEAK[1])).fetchone()["id"]
    db.commit()
    st = terrain.run(tiles=FakeTiles())
    assert st["target"] == 2                                            # 動いた地点と手入力の地点だけ
    assert spot(db, id=b)["elevation_m"] == pytest.approx(105, abs=1.0)
    assert spot(db, id=c)["terrain_status"] == "ok"
    assert spot(db, id=a)["elevation_m"] == pytest.approx(5, abs=0.1)
    terrain.run(all_=True, tiles=FakeTiles())                           # --all は全件
    assert db.execute("select count(*) n from job_runs where job='terrain'").fetchone()["n"] == 4


def test_failed_tile_leaves_spot_for_next_run(db, world):
    sid = add_spot(db, "山頂", *PEAK)
    tx, ty, _, _ = terrain.pixel(*PEAK, 15)
    st = terrain.run(tiles=FakeTiles(broken={("dem5a_png", 15, tx, ty)}))
    assert st["failed"] == 1 and spot(db, id=sid)["terrain_status"] is None
    st = terrain.run(tiles=FakeTiles())
    assert st["ok"] == 1 and spot(db, id=sid)["terrain_status"] == "ok"


def test_tiles_are_fetched_once_per_run(db, world):
    for i in range(5):
        add_spot(db, f"平地{i}", 33.590 + i * 0.0003, 130.405)
    t = FakeTiles()
    terrain.run(tiles=t)
    assert len(t.calls) == len(set(t.calls))


def test_view_and_diagnostics(db, world):
    add_spot(db, "山頂", *PEAK)
    add_spot(db, "海上", 33.630, 130.400)
    add_spot(db, "対象外", 33.590, 130.405, data_status="archived")
    terrain.run(tiles=FakeTiles())
    add_spot(db, "未取得", 33.591, 130.405)
    cols = [r["column_name"] for r in db.execute(
        """select column_name from information_schema.columns where table_name='v_fukuoka_spots' order by ordinal_position""")]
    assert cols[:6] == ["id", "name", "address", "lat", "lng", "elevation_m"]
    names = {r["name"] for r in db.execute("select name from v_fukuoka_spots")}
    assert {"山頂", "海上", "寺A"} <= names and "対象外" not in names     # 住所のない地点も入る・対象外は除く
    d = db.execute("select diag_summary() d").fetchone()["d"]
    assert d["terrain"] == {"ok": 2, "no_data": 1, "pending": 1}      # 山頂・寺A / 海上 / 未取得（対象外は数えない）
    sd = db.execute("select spot_detail(id) d from spots where name='山頂'").fetchone()["d"]
    assert sd["terrain_status"] == "ok" and sd["relief_m"] > 30
