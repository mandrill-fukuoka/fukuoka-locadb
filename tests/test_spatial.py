"""空間特徴（タスクI）のテスト

計算は、手で答えが出せる単純な形（壁・正方形・直線の川）で確かめる。収集・DBへの保存は架空の地図で確かめる。
"""
import json
import math

import pytest
from shapely.geometry import LineString, Polygon, box

from locadb import spatial
from locadb.spatial import Spatial, parse_lanes, parse_width, road_width
from osmworld import World, spots


def built(buildings=(), roads=(), water=()):
    sp = Spatial()
    for b in buildings:
        sp.add_building(b)
    for line, hw, width, lanes in roads:
        sp.add_road(line, hw, width, lanes)
    for g, kind in water:
        sp.add_water(g, kind)
    return sp.build()


# =====================================================================
# 計算
# =====================================================================
def test_empty_surroundings_are_open_and_low_confidence():
    r = built().compute(0, 0)
    assert r["building_cover_100"] == 0 and r["building_count_100"] == 0
    assert r["open_ratio"] == 1.0 and r["sight_max_m"] == spatial.RAY_RANGE_M
    assert r["confidence"] == "low"
    assert r["water_dist_m"] is None and r["road_class"] is None


def test_building_cover_and_count():
    r = built([box(20, -10, 40, 10)]).compute(0, 0)          # 20m四方（400㎡）が半径100mの円の中
    assert r["building_count_100"] == 1
    assert r["building_cover_100"] == pytest.approx(400 / (math.pi * 100 ** 2), abs=0.0004)


def test_cover_counts_only_the_part_inside_the_circle():
    full = built([box(90, -50, 110, 50)]).compute(0, 0)      # 半分以上が円の外
    assert 0 < full["building_cover_100"] < 2000 / (math.pi * 100 ** 2)


def test_openness_against_a_long_wall():
    """東に50m離れた、南北に長い壁。16方向のうち東側7方向だけが壁に当たる（距離は 50÷sin）"""
    wall = box(50, -2000, 60, 2000)
    r = built([wall]).compute(0, 0)
    hits = []
    for k in range(spatial.N_RAYS):
        s = math.sin(k * 2 * math.pi / spatial.N_RAYS)
        d = 50 / s if s > 1e-9 else spatial.RAY_RANGE_M
        hits.append(min(d, spatial.RAY_RANGE_M) if s > 0 else spatial.RAY_RANGE_M)
    assert r["open_ratio"] == pytest.approx(sum(hits) / len(hits) / spatial.RAY_RANGE_M, abs=0.002)
    assert r["sight_max_m"] == spatial.RAY_RANGE_M           # 西・北・南は開けている
    assert r["open_ratio"] < 1.0


def test_enclosed_by_walls_has_short_sight():
    walls = [box(-40, 20, 40, 30), box(-40, -30, 40, -20), box(20, -40, 30, 40), box(-30, -40, -20, 40)]
    r = built(walls).compute(0, 0)
    assert r["sight_max_m"] < 40
    assert r["open_ratio"] < 0.3


def test_the_building_the_spot_stands_in_is_not_a_wall():
    r = built([box(-30, -30, 30, 30)]).compute(0, 0)
    assert r["open_ratio"] == 1.0 and r["sight_max_m"] == spatial.RAY_RANGE_M
    assert r["building_count_100"] == 1                       # 密度には数える


def test_confidence_needs_three_buildings_within_300m():
    two = built([box(20, 0, 30, 10), box(-30, 0, -20, 10)]).compute(0, 0)
    three = built([box(20, 0, 30, 10), box(-30, 0, -20, 10), box(0, 250, 10, 260)]).compute(0, 0)
    assert two["confidence"] == "low" and three["confidence"] == "ok"


def test_water_distance_and_kind_pick_the_nearest():
    river = LineString([(70, -500), (70, 500)])
    pond = box(-30, 0, -20, 10)
    r = built(water=[(river, "river"), (pond, "pond")]).compute(0, 0)
    assert r["water_kind"] == "pond" and r["water_dist_m"] == pytest.approx(20.0, abs=0.1)
    r2 = built(water=[(river, "river")]).compute(0, 0)
    assert r2["water_kind"] == "river" and r2["water_dist_m"] == pytest.approx(70.0, abs=0.1)


def test_road_width_sources_and_snap_distance():
    near = LineString([(10, -100), (10, 100)])
    assert built(roads=[(near, "residential", None, None)]).compute(0, 0)["road_width_m"] == 5.0
    r = built(roads=[(near, "tertiary", None, "2")]).compute(0, 0)
    assert (r["road_class"], r["road_width_m"], r["road_width_src"]) == ("tertiary", 6.0, "lanes")
    r = built(roads=[(near, "tertiary", "7.5 m", "2")]).compute(0, 0)
    assert (r["road_width_m"], r["road_width_src"]) == (7.5, "osm_width")
    far = LineString([(60, -100), (60, 100)])
    assert built(roads=[(far, "primary", None, None)]).compute(0, 0)["road_class"] is None


def test_footways_and_steps_are_not_roads():
    sp = built(roads=[(LineString([(5, -100), (5, 100)]), "footway", None, None),
                      (LineString([(15, -100), (15, 100)]), "residential", None, None)])
    assert sp.compute(0, 0)["road_class"] == "residential"          # 近い歩道ではなく、車道を見る


def test_parsers():
    assert parse_width("6") == 6 and parse_width("6.5 m") == 6.5 and parse_width("12m") == 12
    assert parse_width("wide") is None and parse_width("0") is None and parse_width("500") is None
    assert parse_lanes("2") == 2 and parse_lanes("2;3") is None and parse_lanes("0") is None
    assert road_width("motorway") == (14.0, "class") and road_width("footway") == (None, None)


def test_invalid_polygons_do_not_break_calculation():
    bowtie = Polygon([(10, 10), (30, 30), (30, 10), (10, 30)])       # 自己交差
    r = built([bowtie, box(-40, -5, -30, 5)]).compute(0, 0)
    assert r["building_count_100"] >= 1 and 0 <= r["open_ratio"] <= 1


# =====================================================================
# 収集 → 保存
# =====================================================================
LON, LAT = 130.400, 33.590


def city(w, extra_buildings=3):
    """寺（点）、まわりの建物、東西の車道、少し離れた川"""
    w.point(10, LON, LAT, amenity="place_of_worship", name="寺A")
    for i in range(extra_buildings):
        w.area(900 + i, LON + 0.0003 + 0.0004 * i, LAT + 0.0004, size=0.00015, building="yes")   # 約28・65・102m東
    w.line(950, [(LON - 0.002, LAT - 0.0002), (LON + 0.004, LAT - 0.0002)], highway="residential", lanes="2")
    w.line(951, [(LON - 0.002, LAT - 0.0001), (LON + 0.004, LAT - 0.0001)], highway="footway")   # 車道より近い歩道
    w.line(500, [(LON - 0.003 + 0.001 * i, LAT + 0.003) for i in range(8)], waterway="river", name="那珂川")


def live_ids(db):
    return [s["id"] for s in spots(db, "data_status <> 'archived'")]


def rows(db):
    return {r["spot_id"]: r for r in db.execute("select * from spot_spatial")}


def test_read_collects_buildings_roads_and_water(db, tmp_path):
    w = World(); city(w)
    res = w.run(db, tmp_path)
    c = res["snapshot"].spatial.counts()
    assert c["buildings"] == 3 and c["roads"] == 1 and c["water"] >= 1       # 歩道は道に数えない


def test_run_saves_features_for_every_live_spot_then_skips_unchanged(db, tmp_path):
    w = World(); city(w)
    res = w.run(db, tmp_path)
    st = spatial.run(res["run_id"], res["snapshot"].spatial, min_buildings=1)
    assert st["computed"] == len(live_ids(db)) > 1 and "skipped" not in st
    got = rows(db)
    assert set(got) == set(live_ids(db))
    temple = [s for s in spots(db, "category = 'shrine_temple'")][0]
    r = got[temple["id"]]
    assert r["spatial_version"] == spatial.SPATIAL_VERSION and r["building_count_100"] >= 3
    assert r["road_class"] == "residential" and (r["road_width_m"], r["road_width_src"]) == (6.0, "lanes")
    assert r["water_kind"] == "river" and 200 < r["water_dist_m"] < 500
    assert r["confidence"] == "ok"
    again = spatial.run(res["run_id"], res["snapshot"].spatial, min_buildings=1)
    assert again["target"] == 0 and again["computed"] == 0
    job = db.execute("select stats, error from job_runs where job = 'spatial' order by id desc limit 1").fetchone()
    assert job["error"] is None and job["stats"]["buildings"] == 3


def test_run_recomputes_when_version_changes_or_spot_moves(db, tmp_path, monkeypatch):
    w = World(); city(w)
    res = w.run(db, tmp_path)
    spatial.run(res["run_id"], res["snapshot"].spatial, min_buildings=1)
    before = rows(db)
    temple = [s for s in spots(db, "category = 'shrine_temple'")][0]
    w.point(10, LON + 0.001, LAT, amenity="place_of_worship", name="寺A")           # 約90m東へ
    res = w.run(db, tmp_path)
    st = spatial.run(res["run_id"], res["snapshot"].spatial, min_buildings=1)
    assert st["target"] == 1
    after = rows(db)
    assert after[temple["id"]]["computed_at"] > before[temple["id"]]["computed_at"]
    monkeypatch.setattr(spatial, "SPATIAL_VERSION", "spatial-next")
    st = spatial.run(res["run_id"], res["snapshot"].spatial, min_buildings=1)
    assert st["target"] == len(live_ids(db))
    assert {r["spatial_version"] for r in rows(db).values()} == {"spatial-next"}


def test_run_refuses_when_buildings_look_unread(db, tmp_path):
    """建物が読めていない（取得の不具合）のに、全地点を「建物なし」で上書きしない"""
    w = World(); city(w)
    res = w.run(db, tmp_path)
    st = spatial.run(res["run_id"], res["snapshot"].spatial, min_buildings=10)
    assert "少なすぎ" in st["skipped"] and rows(db) == {}


def test_run_refuses_when_buildings_drop_sharply(db, tmp_path):
    w = World(); city(w, extra_buildings=5)
    res = w.run(db, tmp_path)
    spatial.run(res["run_id"], res["snapshot"].spatial, min_buildings=1)
    w2 = World(); city(w2, extra_buildings=1)
    res2 = w2.run(db, tmp_path)
    before = {k: v["computed_at"] for k, v in rows(db).items()}
    st = spatial.run(res2["run_id"], res2["snapshot"].spatial, all_=True, min_buildings=1)
    assert "大きく減って" in st["skipped"]
    assert {k: v["computed_at"] for k, v in rows(db).items()} == before


def test_dry_run_reports_distribution_and_writes_nothing(db, tmp_path):
    w = World(); city(w)
    res = w.run(db, tmp_path, mode="dry_run")
    st = spatial.dry_run(res["snapshot"].spatial, min_buildings=1)
    assert rows(db) == {}
    assert st["dry_run"] is True and "distribution" in st and "compute_seconds" in st
    json.dumps(st, ensure_ascii=False)                                  # 報告に書き出せる形


def test_dry_run_works_before_the_table_exists(db, tmp_path):
    """DB変更190の適用前の本番でも、試し実行で時間と分布を測れる"""
    w = World(); city(w)
    res = w.run(db, tmp_path)                       # 地点を入れてから、表だけ無い状態にする
    db.execute("drop table spot_spatial")
    db.commit()
    st = spatial.dry_run(res["snapshot"].spatial, min_buildings=1)
    assert st["target"] > 0 and "distribution" in st


# =====================================================================
# 公開画面に出す段階（spatial_view。境界は暫定：仕様書10章タスクI）
# =====================================================================
def view(db, **cols):
    base = dict(confidence="ok", open_ratio=0.5, building_cover_100=0.2, water_dist_m=None, water_kind=None,
                road_class=None, road_width_m=None)
    base.update(cols)
    sid = db.execute("select id from spots where name = '寺A'").fetchone()["id"]
    db.execute("delete from spot_spatial where spot_id = %s", (sid,))
    db.execute("""insert into spot_spatial (spot_id, spatial_version, confidence, open_ratio, building_cover_100,
                  water_dist_m, water_kind, road_class, road_width_m)
                  values (%s,'t',%s,%s,%s,%s,%s,%s,%s)""",
               (sid, base["confidence"], base["open_ratio"], base["building_cover_100"], base["water_dist_m"],
                base["water_kind"], base["road_class"], base["road_width_m"]))
    return db.execute("select spot_detail(%s) d", (sid,)).fetchone()["d"]["spatial"]


@pytest.fixture
def temple(db, tmp_path):
    w = World(); city(w); w.run(db, tmp_path)


def test_view_is_null_before_computation(db, temple):
    sid = db.execute("select id from spots where name = '寺A'").fetchone()["id"]
    assert db.execute("select spot_detail(%s) d", (sid,)).fetchone()["d"]["spatial"] is None


@pytest.mark.parametrize("v,label", [(0.7, "開けている"), (0.699, "ふつう"), (0.35, "ふつう"), (0.349, "囲まれている")])
def test_view_open_bands(db, temple, v, label):
    assert view(db, open_ratio=v)["open"] == label


@pytest.mark.parametrize("v,label", [(0.149, "少ない"), (0.15, "中くらい"), (0.299, "中くらい"), (0.30, "多い")])
def test_view_built_bands(db, temple, v, label):
    assert view(db, building_cover_100=v)["built"] == label


@pytest.mark.parametrize("d,band", [(0, "すぐそば"), (49.9, "すぐそば"), (50, "近い"), (149.9, "近い"), (150, "なし"), (None, "なし")])
def test_view_water_bands(db, temple, d, band):
    sp = view(db, water_dist_m=d, water_kind="river" if d is not None else None)
    assert sp["water"] == band
    assert (sp["water_kind"] == "river") == (band != "なし")           # 「なし」では種類を出さない


@pytest.mark.parametrize("w,label", [(3.5, "狭い道"), (4.99, "狭い道"), (5.0, "ふつうの道"), (6.49, "ふつうの道"), (6.5, "広い道")])
def test_view_road_bands(db, temple, w, label):
    assert view(db, road_class="residential", road_width_m=w)["road"] == label
    assert view(db, road_class=None, road_width_m=None)["road"] is None


def test_view_low_confidence_has_no_bands(db, temple):
    assert view(db, confidence="low") == {"status": "low"}              # 段階は出さず「未判定」


def test_view_never_exposes_sight_range(db, temple):
    assert "sight_max_m" not in json.dumps(view(db), ensure_ascii=False)
