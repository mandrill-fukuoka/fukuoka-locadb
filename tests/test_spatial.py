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


# =====================================================================
# 絞り込み検索（search_spots の p_open / p_water / p_road）
# =====================================================================
def put(db, name, **cols):
    sid = db.execute("select id from spots where name = %s", (name,)).fetchone()["id"]
    base = dict(confidence="ok", open_ratio=0.5, building_cover_100=0.2, water_dist_m=None, water_kind=None,
                road_class=None, road_width_m=None)
    base.update(cols)
    db.execute("delete from spot_spatial where spot_id = %s", (sid,))
    db.execute("""insert into spot_spatial (spot_id, spatial_version, confidence, open_ratio, building_cover_100,
                  water_dist_m, water_kind, road_class, road_width_m) values (%s,'t',%s,%s,%s,%s,%s,%s,%s)""",
               (sid, base["confidence"], base["open_ratio"], base["building_cover_100"], base["water_dist_m"],
                base["water_kind"], base["road_class"], base["road_width_m"]))
    return sid


def found(db, **args):
    keys = ", ".join(f"{k} => %s" for k in args)
    rows = db.execute(f"select search_spots({keys}) r", tuple(args.values())).fetchone()["r"]
    return {r["name"] for r in rows}


def test_search_filters_by_open_water_road(db, tmp_path):
    w = World(); city(w)
    w.point(11, LON + 0.01, LAT, amenity="place_of_worship", name="寺B")
    w.point(12, LON + 0.02, LAT, amenity="place_of_worship", name="寺C")
    w.point(13, LON + 0.03, LAT, amenity="place_of_worship", name="寺D")
    w.run(db, tmp_path)
    put(db, "寺A", open_ratio=0.9, water_dist_m=30, water_kind="river", road_class="residential", road_width_m=3.5)
    put(db, "寺B", open_ratio=0.2, water_dist_m=100, water_kind="pond", road_class="primary", road_width_m=8)
    put(db, "寺C", confidence="low", open_ratio=0.9, water_dist_m=10, road_class="residential", road_width_m=3.5)
    # 寺D は空間特徴が未計算
    assert found(db, p_open="open") == {"寺A"}                       # 信頼度lowの寺Cは対象外
    assert found(db, p_open="closed") == {"寺B"}
    assert found(db, p_water="near") == {"寺A"}                      # 50m未満
    assert found(db, p_water="within150") == {"寺A", "寺B"}          # 150m未満
    assert found(db, p_road="narrow") == {"寺A"} and found(db, p_road="wide") == {"寺B"}
    assert found(db, p_open="open", p_water="near", p_road="narrow") == {"寺A"}    # 重ねると両方満たすもの
    assert found(db, p_open="closed", p_water="near") == set()
    assert {"寺A", "寺B", "寺C", "寺D"} <= found(db, p_category="shrine_temple")   # 指定がなければ全部（未計算も）


def test_search_boundaries_match_the_view(db, tmp_path):
    w = World(); city(w); w.run(db, tmp_path)
    for d, near, within in ((49.9, True, True), (50, False, True), (149.9, False, True), (150, False, False)):
        put(db, "寺A", water_dist_m=d, water_kind="river")
        assert (("寺A" in found(db, p_water="near")), ("寺A" in found(db, p_water="within150"))) == (near, within)
    for v, label in ((0.7, "open"), (0.699, "mid"), (0.349, "closed")):
        sid = put(db, "寺A", open_ratio=v)
        got = db.execute("select spot_detail(%s) d", (sid,)).fetchone()["d"]["spatial"]["open"]
        assert got == {"open": "開けている", "mid": "ふつう", "closed": "囲まれている"}[label]
        assert ("寺A" in found(db, p_open="open")) == (label == "open")


# =====================================================================
# 一覧カード・建物の多さ・ルールのタグ
# =====================================================================
def test_search_returns_spatial_view_and_filters_by_built(db, tmp_path):
    w = World(); city(w)
    w.point(11, LON + 0.01, LAT, amenity="place_of_worship", name="寺B")
    w.run(db, tmp_path)
    put(db, "寺A", building_cover_100=0.10)
    put(db, "寺B", building_cover_100=0.30)
    assert found(db, p_built="few") == {"寺A"} and found(db, p_built="many") == {"寺B"}
    rows = db.execute("select search_spots(p_q => '寺A') r").fetchone()["r"]
    assert rows[0]["spatial"]["built"] == "少ない"


def rule_tags(db):
    return {(r["name"], r["tag_code"]) for r in db.execute(
        "select s.name, st.tag_code from spot_tags st join spots s on s.id = st.spot_id where st.origin = 'rule'")}


def test_rule_tags_follow_spatial_features_and_category(db, tmp_path):
    w = World(); city(w)
    w.point(11, LON + 0.01, LAT, amenity="place_of_worship", name="寺B")
    w.run(db, tmp_path)
    put(db, "寺A", open_ratio=0.9, road_class="residential", road_width_m=3.5)
    put(db, "寺B", confidence="low", open_ratio=0.9, road_class="residential", road_width_m=3.5)
    db.execute("update spots set category='park' where name='寺B'")
    st = db.execute("select rule_tags_apply() r").fetchone()["r"]
    assert rule_tags(db) == {("寺A", "wide_open"), ("寺A", "narrow_street"), ("寺B", "greenery")}   # lowは空間の規則に使わない
    assert st["added"] == 3 and st["by_tag"]["wide_open"] == 1
    again = db.execute("select rule_tags_apply() r").fetchone()["r"]
    assert again["added"] == 0 and again["removed"] == 0                                         # 何度やっても同じ
    put(db, "寺A", open_ratio=0.5, road_class="residential", road_width_m=3.5)                  # 条件を外れたら消える
    db.execute("select rule_tags_apply()")
    assert ("寺A", "wide_open") not in rule_tags(db) and ("寺A", "narrow_street") in rule_tags(db)


def test_rule_tags_never_override_human_or_ai_and_survive_review_save(db, tmp_path):
    w = World(); city(w); w.run(db, tmp_path)
    sid = put(db, "寺A", open_ratio=0.9)
    db.execute("insert into spot_tags (spot_id, tag_code, origin, score) values (%s,'wide_open','human',1.0)", (sid,))
    db.execute("select rule_tags_apply()")
    assert db.execute("select origin from spot_tags where spot_id=%s and tag_code='wide_open'", (sid,)).fetchone()["origin"] == "human"
    db.execute("delete from spot_tags where spot_id=%s", (sid,))
    db.execute("select rule_tags_apply()")
    assert rule_tags(db) == {("寺A", "wide_open")}
    db.execute("select review_spot(%s, 'candidate', 'desk_checked', null, %s, %s)", (sid, ["calm"], "tester"))                          # 人が別のタグを保存しても
    assert ("寺A", "wide_open") in rule_tags(db)                                                  # ルールのタグは消えない
    pub = db.execute("select search_spots(p_q => '寺A') r").fetchone()["r"][0]
    assert pub["rule_tags"] == ["開けた空間"] and pub["tags"] == ["穏やか"]                         # 人のタグとは別に出る


def test_pipeline_step_records_a_job_run(db, tmp_path):
    from locadb import ruletags
    w = World(); city(w); res = w.run(db, tmp_path)
    put(db, "寺A", open_ratio=0.9); db.commit()
    st = ruletags.run(res["run_id"])
    assert st["by_tag"].get("wide_open") == 1
    job = db.execute("select stats, error from job_runs where job = 'rule_tags' order by id desc limit 1").fetchone()
    assert job["error"] is None and job["stats"]["added"] >= 1


# =====================================================================
# 一覧の並び順・確認状態の段階（2026-10-21 提案1）
# =====================================================================
def test_search_orders_rich_spots_first_and_exposes_fact_count(db, tmp_path):
    w = World(); city(w)
    w.point(11, LON + 0.01, LAT, amenity="place_of_worship", name="寺B")
    w.point(12, LON + 0.02, LAT, amenity="place_of_worship", name="寺C")
    w.run(db, tmp_path)
    sid = db.execute("select id from spots where name = '寺C'").fetchone()["id"]
    db.execute("""insert into spot_facts (spot_id, item, value, source_kind, source_url, approved_by)
                  values (%s, '管理者・所有者', 'テスト法人', 'web', 'https://example.org/x', 't')""", (sid,))
    rows = db.execute("select search_spots(p_category => 'shrine_temple') r").fetchone()["r"]
    names = [r["name"] for r in rows]
    assert names[0] == "寺C" and rows[0]["fact_count"] == 1          # 事実のある地点が先
    again = [r["name"] for r in db.execute("select search_spots(p_category => 'shrine_temple') r").fetchone()["r"]]
    assert names == again                                            # 並びは毎回同じ（ページ送りが安定）


def test_unnamed_coastline_and_river_count_as_water(db, tmp_path):
    """名前のない海岸線（natural=coastline）も水辺に数える。那の津などの海沿いが「水辺なし」にならないように"""
    w = World(); city(w)
    # 寺Aの約55m南を東西に走る、名前のない海岸線
    w.line(501, [(LON - 0.002, LAT - 0.0005), (LON + 0.004, LAT - 0.0005)], natural="coastline")
    res = w.run(db, tmp_path)
    assert res["snapshot"].spatial.counts()["water"] >= 2
    spatial.run(res["run_id"], res["snapshot"].spatial, min_buildings=1)
    temple = [s for s in spots(db, "category = 'shrine_temple'")][0]
    r = rows(db)[temple["id"]]
    assert r["water_kind"] == "coast" and r["water_dist_m"] < 100        # 川（200m超）より海岸線が近い


# =====================================================================
# 名前のない地点：近くの目印で名付ける／一覧では同名をまとめる（2026-10-21 提案1）
# =====================================================================
def test_unnamed_spot_without_address_is_named_after_a_landmark(db, tmp_path, monkeypatch):
    from locadb import address
    w = World()
    w.point(20, LON, LAT, railway="station", name="和白駅", public_transport="station")
    w.point(21, LON + 0.004, LAT, natural="beach")                       # 名前のない砂浜（約370m東）
    w.run(db, tmp_path)
    monkeypatch.setattr(address, "lookup", lambda lon, lat, session=None: None)      # 海辺：住所が引けない
    st = address.run()
    assert st["landmark_named"] == 1
    names = {r["name"] for r in db.execute("select name from spots where name like '（無名）%'")}
    assert any("和白駅付近" in n for n in names), names


def test_search_groups_unnamed_and_slopes_of_the_same_name(db, tmp_path):
    w = World(); city(w)
    for i in range(3):
        w.point(30 + i, LON + 0.01 + 0.0005 * i, LAT, natural="beach")     # 名前のない砂浜3か所（同じ名前・同じ区）
    w.run(db, tmp_path)
    rows = db.execute("select search_spots(p_category => 'beach') r").fetchone()["r"]
    assert len(rows) == 1 and rows[0]["others"] == 2                        # 代表点1件＋「ほか2か所」
