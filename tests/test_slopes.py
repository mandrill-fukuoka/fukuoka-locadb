"""回帰テスト：坂道の検出（locadb/slopes.py）と、坂道の地点の反映

架空の地形：中心 (33.590, 130.390)、半径400m・高さ100m の円錐の山（勾配25%）。ほかは標高5mの平地。
"""
import math

import numpy as np
import pytest

from demworld import DemTiles
from locadb import slopes, terrain
from osmworld import World, spot, spots

PEAK = (33.590, 130.390)
DLON = 1 / (terrain.M_PER_DEG * math.cos(math.radians(PEAK[0])))     # 経度1m
DLAT = 1 / terrain.M_PER_DEG


def height(lat, lon):
    d = np.hypot((lat - PEAK[0]) * terrain.M_PER_DEG, (lon - PEAK[1]) / DLON)
    return 5.0 + np.maximum(0.0, 100.0 * (1.0 - d / 400.0))


def east(r):           # 山頂から東へ r m の点 (lon, lat)
    return (PEAK[1] + r * DLON, PEAK[0])


def north(r):
    return (PEAK[1], PEAK[0] + r * DLAT)


def south(r):
    return (PEAK[1], PEAK[0] - r * DLAT)


@pytest.fixture
def hill(monkeypatch):
    monkeypatch.setattr(slopes, "make_tiles", lambda: DemTiles(height))


def base_world():
    w = World()
    w.point(10, 130.385, 33.585, amenity="place_of_worship", name="寺A")
    return w


def slope_spots(db):
    return spots(db, "category='slope_street' and data_status <> 'archived'")


def test_radial_road_steps_flat_and_bridge(db, tmp_path, hill):
    w = base_world()
    w.line(100, [east(r) for r in range(50, 351, 50)], highway="residential")                 # 東の斜面を上る道
    w.line(101, [(130.420 + i * 0.001, 33.595) for i in range(5)], highway="residential")      # 平地
    w.line(102, [south(r) for r in range(60, 301, 60)], highway="residential", bridge="yes")   # 橋（除く）
    w.line(103, [north(100), north(130)], highway="steps", name="愛宕坂")                        # 階段 高低差7.5m
    res = w.run(db, tmp_path)
    assert res["status"] == "ok"
    got = sorted(slope_spots(db), key=lambda s: s["slope_kind"])
    assert [s["slope_kind"] for s in got] == ["road", "steps"]
    road, steps = got
    assert road["name"].startswith("（無名）") and road["slope_grade_pct"] == pytest.approx(25, abs=2)
    assert road["slope_len_m"] >= 250 and road["slope_rise_m"] == pytest.approx(75, abs=6)
    assert abs(((road["slope_bearing"] - 270) + 180) % 360 - 180) <= 5          # 西（山頂の方）へ上る
    assert steps["name"] == "愛宕坂" and steps["slope_rise_m"] == pytest.approx(7.5, abs=1.5)
    assert abs(((steps["slope_bearing"] - 180) + 180) % 360 - 180) <= 5         # 南（山頂の方）へ上る
    g = db.execute("select landscape_group from spot_categories where code='slope_street'").fetchone()
    assert g["landscape_group"] == "slope"


def test_split_way_is_one_slope_and_stable(db, tmp_path, hill):
    """1本の坂がOSMで2つの way に分かれても1地点。後から分割・番号が変わっても同じ地点のまま"""
    w = base_world()
    w.line(200, [east(r) for r in range(50, 351, 25)], highway="residential")
    w.run(db, tmp_path)
    first = slope_spots(db)
    assert len(first) == 1
    w.remove("ways", 200)
    w.line(201, [east(r) for r in range(50, 201, 25)], highway="residential")
    w.line(202, [east(r) for r in range(200, 351, 25)], highway="residential")
    w.run(db, tmp_path); w.run(db, tmp_path)
    after = slope_spots(db)
    assert [s["id"] for s in after] == [first[0]["id"]]
    assert db.execute("select count(*) n from spots where category='slope_street'").fetchone()["n"] == 1


def test_nearby_parallel_slopes_merge(db, tmp_path, hill):
    """同じ坂を通る別の道（歩道と車道など）は1地点にまとめる"""
    w = base_world()
    w.line(300, [east(r) for r in range(50, 351, 50)], highway="residential")
    w.line(301, [(x, y + 10 * DLAT) for x, y in (east(r) for r in range(50, 351, 50))], highway="footway")
    w.run(db, tmp_path)
    assert len(slope_spots(db)) == 1


def test_too_steep_vehicular_road_is_data_error():
    tiles = DemTiles(lambda lat, lon: 5.0 + np.maximum(0, (lon - 130.40) / DLON) * 0.4)   # 東へ40%
    coords = [(130.40 + r * DLON, 33.59) for r in range(0, 201, 20)]
    car = slopes.Road(1, "residential", "", coords)
    walk = slopes.Road(2, "footway", "", coords)
    assert slopes.find(car, tiles) == []
    s = slopes.find(walk, tiles)
    assert len(s) == 1 and s[0].grade_pct == pytest.approx(40, abs=2)


def test_gentle_slope_below_threshold():
    tiles = DemTiles(lambda lat, lon: 5.0 + np.maximum(0, (lon - 130.40) / DLON) * 0.06)  # 6%
    coords = [(130.40 + r * DLON, 33.59) for r in range(0, 301, 20)]
    assert slopes.find(slopes.Road(1, "residential", "", coords), tiles) == []


def test_failed_tiles_keep_existing_slopes(db, tmp_path, monkeypatch):
    """標高タイルが取れなかった回は、坂道を消失扱いにしない"""
    monkeypatch.setattr(slopes, "make_tiles", lambda: DemTiles(height))
    w = base_world()
    w.line(400, [east(r) for r in range(50, 351, 50)], highway="residential")
    w.run(db, tmp_path)
    sid = slope_spots(db)[0]["id"]
    tx, ty, _, _ = terrain.pixel(PEAK[0], east(200)[0], 15)
    monkeypatch.setattr(slopes, "make_tiles", lambda: DemTiles(height, broken={("dem5a_png", 15, tx, ty)}))
    for _ in range(3):
        res = w.run(db, tmp_path)
    s = spot(db, id=sid)
    assert s["data_status"] == "current" and s["missing_runs"] == 0
    assert "元データの取得が不完全" in res["report"]


def test_slope_removed_from_osm_is_archived_after_two_runs(db, tmp_path, hill):
    w = base_world()
    w.line(500, [east(r) for r in range(50, 351, 50)], highway="residential")
    w.run(db, tmp_path)
    sid = slope_spots(db)[0]["id"]
    w.remove("ways", 500)
    w.run(db, tmp_path)
    assert spot(db, id=sid)["data_status"] == "current"
    w.run(db, tmp_path)
    assert spot(db, id=sid)["data_status"] == "archived"


def test_search_relief_filter_and_detail(db, tmp_path, hill):
    w = base_world()
    w.point(11, PEAK[1], PEAK[0], leisure="park", name="山頂公園")                              # 周りより37.5m高い
    w.line(600, [east(r) for r in range(50, 351, 50)], highway="residential")
    w.run(db, tmp_path)
    from locadb import terrain as T
    from demworld import DemTiles as D
    T.run(tiles=D(height))
    hi = db.execute("select search_spots(p_relief_min => 10) r").fetchone()["r"]
    assert [x["name"] for x in hi] == ["山頂公園"] and hi[0]["relief_m"] == pytest.approx(37.5, abs=1.5)
    s = db.execute("select spot_detail(id) d from spots where category='slope_street'").fetchone()["d"]
    assert s["slope_kind"] == "road" and s["slope_grade_pct"] > 20 and s["grp_code"] == "slope"
