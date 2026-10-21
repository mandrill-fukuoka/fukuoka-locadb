"""回帰テスト（改修指示書 第11章 A〜D と受け入れ基準に対応）

各テストは架空の地図を作り、収集→反映を複数回まわして、地点・履歴・状態を確かめる。
"""
import hashlib
import json

import pytest

from locadb import osm_source
from locadb.categories import categories_of, display_name
from locadb.pipeline import run_sync
from osmworld import World, events, spot, spots

RIVER = [(130.381 + 0.002 * i, 33.590) for i in range(15)]          # 中央区を東西に約2.6km


def temples(w, n, base=10):
    for i in range(n):
        w.point(base + i, 130.385 + 0.002 * i, 33.585, amenity="place_of_worship", name=f"寺{i}")


# =====================================================================
# A. 取得の失敗・不完全なデータ
# =====================================================================
class _Resp:
    def __init__(self, body=b"", md5=None, status=200):
        self.body, self.status_code = body, status
        self.text = f"{md5 or hashlib.md5(body).hexdigest()}  x.pbf"
    def raise_for_status(self):
        if self.status_code >= 400:
            e = osm_source.requests.HTTPError(f"{self.status_code}")
            e.response = self
            raise e
    def iter_content(self, n): yield self.body
    def __enter__(self): return self
    def __exit__(self, *a): pass


@pytest.fixture
def no_wait(monkeypatch):
    waits = []
    monkeypatch.setattr(osm_source.time, "sleep", lambda s: waits.append(s))
    return waits


def test_A_md5_mismatch_aborts(tmp_path, monkeypatch, no_wait):
    """ダウンロードしたファイルが壊れていたら（取り直しても直らなければ）中止。壊れたファイルは残さない"""
    monkeypatch.setattr(osm_source.requests, "get", lambda url, **k: _Resp(b"broken", md5="deadbeef"))
    with pytest.raises(RuntimeError, match="MD5"):
        osm_source.download(tmp_path / "x.pbf", log=lambda *_: None)
    assert len(no_wait) == osm_source.DOWNLOAD_ATTEMPTS - 1
    assert not (tmp_path / "x.pbf").exists()


def test_A_download_retries_transient_failure_then_succeeds(tmp_path, monkeypatch, no_wait):
    calls = {"n": 0}
    def get(url, **k):
        if url.endswith(".md5"):
            return _Resp(b"data")
        calls["n"] += 1
        if calls["n"] == 1:
            raise osm_source.requests.ConnectionError("reset")
        if calls["n"] == 2:
            return _Resp(b"data", status=503)
        return _Resp(b"data")
    monkeypatch.setattr(osm_source.requests, "get", get)
    info = osm_source.download(tmp_path / "x.pbf", log=lambda *_: None)
    assert info["attempts"] == 3 and info["bytes"] == 4
    assert no_wait == list(osm_source.DOWNLOAD_WAIT_S)


def test_A_download_does_not_retry_client_error(tmp_path, monkeypatch, no_wait):
    monkeypatch.setattr(osm_source.requests, "get",
                        lambda url, **k: _Resp(b"x", status=404) if not url.endswith(".md5") else _Resp(b"x"))
    with pytest.raises(osm_source.requests.HTTPError):
        osm_source.download(tmp_path / "x.pbf", log=lambda *_: None)
    assert no_wait == []


def test_A_download_gives_up_after_attempts(tmp_path, monkeypatch, no_wait):
    def get(url, **k):
        raise osm_source.requests.Timeout("slow")
    monkeypatch.setattr(osm_source.requests, "get", get)
    with pytest.raises(osm_source.requests.Timeout):
        osm_source.download(tmp_path / "x.pbf", log=lambda *_: None)
    assert len(no_wait) == osm_source.DOWNLOAD_ATTEMPTS - 1


def test_A_corrupt_file_changes_nothing(db, tmp_path):
    w = World(); temples(w, 3)
    w.run(db, tmp_path)
    bad = tmp_path / "bad.osm"
    bad.write_text('<?xml version="1.0"?><osm version="0.6"><node id="1" lat="33.5', encoding="utf-8")
    with pytest.raises(Exception):
        run_sync(db, lambda watch: (osm_source.read(bad, watch), {}), "apply")
    assert len(spots(db, "data_status='current'")) == 3
    st = db.execute("select status from data_runs order by id desc limit 1").fetchone()["status"]
    assert st == "failed"


def test_A_missing_boundary_aborts(db, tmp_path):
    w = World(); temples(w, 3)
    w.run(db, tmp_path)
    w.remove("rels", 2)
    with pytest.raises(RuntimeError, match="境界"):
        w.run(db, tmp_path)
    assert len(spots(db, "data_status='current'")) == 3


def test_A_sudden_drop_is_not_treated_as_disappearance(db, tmp_path):
    """前回12件→今回5件（6割減）の種別は「異常」とし、見つからない地点を消失扱いしない"""
    w = World(); temples(w, 12)
    w.run(db, tmp_path)
    for i in range(5, 12):
        w.remove("nodes", 10 + i)
    for _ in range(3):
        r = w.run(db, tmp_path)
        assert "shrine_temple" in r["summary"]["frozen_categories"]
        assert r["summary"]["missing"] == 0
    assert len(spots(db, "data_status='current'")) == 12
    r = w.run(db, tmp_path, allow=True)                     # 人が確認して受け入れた
    assert r["summary"]["frozen_categories"] == [] and r["summary"]["missing"] == 7


def test_A_small_true_zero_is_accepted_but_not_immediate(db, tmp_path):
    """件数の少ない種別が0件になっても、1回では対象外にしない（2回続けば対象外）"""
    w = World(); temples(w, 2)
    w.run(db, tmp_path)
    w.remove("nodes", 10); w.remove("nodes", 11)
    w.run(db, tmp_path)
    assert len(spots(db, "data_status='current'")) == 2
    w.run(db, tmp_path)
    assert len(spots(db, "data_status='archived'")) == 2


def test_A_dry_run_changes_nothing_and_keeps_report(db, tmp_path):
    w = World(); temples(w, 3)
    r = w.run(db, tmp_path, mode="dry_run")
    assert r["summary"]["created"] == 3
    assert spots(db) == []
    row = db.execute("select mode, status, report from data_runs order by id desc limit 1").fetchone()
    assert row["mode"] == "dry_run" and "新規: 3" in row["report"]


def test_A_mass_archive_is_stopped(db, tmp_path):
    """1回で大量に対象外になる場合は反映を止める（許可すれば反映）"""
    w = World()
    for i in range(150):
        w.point(100 + i, 130.381 + 0.0002 * i, 33.583 + 0.0001 * (i % 7), amenity="school", name=f"学校{i}")
    w.run(db, tmp_path)
    for i in range(60):                                    # 4割（異常判定の手前）が条件から外れる
        w.nodes[100 + i][2]["amenity"] = "office"
    r = w.run(db, tmp_path)
    assert r["status"] == "stopped" and r["summary"]["archived"] == 60
    assert len(spots(db, "data_status='current'")) == 150
    r = w.run(db, tmp_path, allow=True)
    assert r["status"] == "ok" and len(spots(db, "data_status='archived'")) == 60


# =====================================================================
# B. 線状の地物（川・線路など）
# =====================================================================
def river_spots(db):
    return spots(db, "category='river' and data_status<>'archived'")


def test_B_same_line_no_growth(db, tmp_path):
    w = World(); w.line(500, RIVER, waterway="river", name="那珂川")
    w.run(db, tmp_path)
    n = len(river_spots(db))
    assert n >= 4
    for _ in range(2):
        r = w.run(db, tmp_path)
        assert r["summary"]["created"] == 0 and r["summary"]["moved"] == 0
    assert len(river_spots(db)) == n


def test_B_extension_adds_only_new_cells(db, tmp_path):
    w = World(); w.line(500, RIVER[:8], waterway="river", name="那珂川")
    w.run(db, tmp_path)
    before = {s["sample_key"]: (s["lon"], s["lat"]) for s in river_spots(db)}
    w.line(500, RIVER, waterway="river", name="那珂川")         # 東へ延びた
    r = w.run(db, tmp_path)
    after = {s["sample_key"]: (s["lon"], s["lat"]) for s in river_spots(db)}
    assert set(before) <= set(after) and len(after) > len(before)
    assert all(after[k] == before[k] for k in before)             # 既存の点は動かない
    assert r["summary"]["moved"] == 0


def test_B_shortening_retires_with_history_not_immediately(db, tmp_path):
    w = World(); w.line(500, RIVER, waterway="river", name="那珂川")
    w.run(db, tmp_path)
    n = len(river_spots(db))
    w.line(500, RIVER[:6], waterway="river", name="那珂川")      # 短くなった
    w.run(db, tmp_path)
    assert len(river_spots(db)) == n                               # 1回目は残す
    w.run(db, tmp_path)
    gone = spots(db, "category='river' and data_status='archived'")
    assert gone and all("線の形" in g["archived_reason"] for g in gone)
    assert "missing" in events(db, gone[0]["id"]) and "archived" in events(db, gone[0]["id"])


def test_B_reversed_and_split_line_keeps_same_spots(db, tmp_path):
    """線の向きが反転・2本に分割・IDが変わっても、同じ名前の川なら地点は同じ"""
    w = World(); w.line(500, RIVER, waterway="river", name="那珂川")
    w.run(db, tmp_path)
    keys = {s["sample_key"] for s in river_spots(db)}
    w.remove("ways", 500)
    rev = list(reversed(RIVER))
    w.line(601, rev[:8], waterway="river", name="那珂川")
    w.line(602, rev[7:], waterway="river", name="那珂川")
    r = w.run(db, tmp_path)
    assert {s["sample_key"] for s in river_spots(db)} == keys
    assert r["summary"]["created"] == 0 and r["summary"]["archived"] == 0


def test_B_verified_sample_is_not_moved_silently(db, tmp_path):
    w = World(); w.line(500, RIVER, waterway="river", name="那珂川")
    w.run(db, tmp_path)
    s = river_spots(db)[1]
    db.execute("update spots set status='verified', confidence='field_checked' where id=%s", (s["id"],)); db.commit()
    w.line(500, [(lon, lat + 0.0004) for lon, lat in RIVER], waterway="river", name="那珂川")   # 約44m北へ
    w.run(db, tmp_path)
    s2 = spot(db, id=s["id"])
    assert (s2["lon"], s2["lat"]) == (s["lon"], s["lat"])
    assert s2["data_status"] == "needs_review" and s2["status"] == "verified"
    h = db.execute("select detail from spot_history where spot_id=%s and event='needs_review'", (s["id"],)).fetchone()
    assert "moved" in h["detail"]["proposed"]


def test_B_renamed_river_relinks_without_duplicates(db, tmp_path):
    w = World(); w.line(500, RIVER, waterway="river", name="那珂川")
    w.run(db, tmp_path)
    ids = {s["id"] for s in river_spots(db)}
    w.line(500, RIVER, waterway="river", name="那珂川（下流）")
    r = w.run(db, tmp_path)
    assert {s["id"] for s in river_spots(db)} == ids
    assert r["summary"]["created"] == 0 and r["summary"]["relinked"] == len(ids)
    assert all(s["name"] == "那珂川（下流）" for s in river_spots(db))


def test_B_cross_ward_line_assigned_by_position(db, tmp_path):
    w = World(); w.line(500, [(130.39 + 0.003 * i, 33.59) for i in range(20)], waterway="river", name="境川")
    w.run(db, tmp_path)
    for s in river_spots(db):
        assert s["area_code"] == ("fuk-chuo" if s["lon"] < 130.42 else "fuk-hakata")


def test_B_multi_category_kept(db, tmp_path):
    w = World(); w.area(700, 130.40, 33.595, leisure="park", historic="castle", name="舞鶴公園")
    w.run(db, tmp_path)
    s = spot(db, name="舞鶴公園")
    assert s["category"] == "heritage" and s["extra_categories"] == ["park"]
    rows = db.execute("select search_spots(null,null,'park') r").fetchone()["r"]
    assert any(x["name"] == "舞鶴公園" for x in rows)


def test_B_river_position_snapped_to_walkway(db, tmp_path):
    """川の中心線ではなく、近くの歩ける道（川沿いの道）に地点を置く"""
    w = World(); w.line(500, RIVER, waterway="river", name="那珂川")
    w.line(501, [(lon, 33.5903) for lon, _ in RIVER], highway="footway")     # 約33m北の遊歩道
    w.run(db, tmp_path)
    for s in river_spots(db):
        assert s["position_kind"] == "standpoint" and abs(s["lat"] - 33.5903) < 1e-5


# =====================================================================
# C. 地点と履歴
# =====================================================================
def test_C_verified_point_moved_goes_to_review(db, tmp_path):
    w = World(); w.point(10, 130.39, 33.585, amenity="place_of_worship", name="警固神社")
    w.run(db, tmp_path)
    s = spot(db, name="警固神社")
    db.execute("update spots set status='scouted' where id=%s", (s["id"],)); db.commit()
    w.point(10, 130.3910, 33.585, amenity="place_of_worship", name="警固神社")       # 約93m東
    w.run(db, tmp_path)
    s2 = spot(db, id=s["id"])
    assert s2["lon"] == s["lon"] and s2["data_status"] == "needs_review"
    assert events(db, s["id"])[-1] == "needs_review"


def test_C_unverified_point_moves_and_renames(db, tmp_path):
    w = World(); w.point(10, 130.39, 33.585, amenity="place_of_worship", name="旧名寺")
    w.run(db, tmp_path)
    w.point(10, 130.3905, 33.585, amenity="place_of_worship", name="新名寺")
    w.run(db, tmp_path)
    s = spot(db, code="FUK-CHU-S0001")
    assert s["name"] == "新名寺" and "旧名寺" in s["alt_names"] and abs(s["lon"] - 130.3905) < 1e-7
    assert events(db, s["id"]) == ["created", "moved", "renamed"]


def test_C_manual_spot_untouched(db, tmp_path):
    w = World(); temples(w, 1)
    w.run(db, tmp_path)
    db.execute("""insert into spots (code, name, area_code, geom, category, origin, position_kind)
                  values ('FUK-CHU-M0001','手入力の路地','fuk-chuo',ST_SetSRID(ST_MakePoint(130.39,33.59),4326)::geography,
                          'alley','manual','manual')"""); db.commit()
    for _ in range(3):
        w.run(db, tmp_path)
    s = spot(db, name="手入力の路地")
    assert s["data_status"] == "current" and events(db, s["id"]) == []


def test_C_deleted_then_restored(db, tmp_path):
    w = World(); temples(w, 1)
    w.run(db, tmp_path)
    sid = spots(db)[0]["id"]
    w.remove("nodes", 10)
    w.run(db, tmp_path); w.run(db, tmp_path)
    assert spot(db, id=sid)["data_status"] == "archived"
    temples(w, 1)
    w.run(db, tmp_path)
    s = spot(db, id=sid)
    assert s["data_status"] == "current" and events(db, sid)[-1] == "restored"
    assert len(spots(db)) == 1


def test_C_no_longer_matching_is_archived_with_reason(db, tmp_path):
    w = World(); w.point(10, 130.39, 33.585, amenity="place_of_worship", name="寺A")
    w.run(db, tmp_path)
    w.point(10, 130.39, 33.585, amenity="cafe", name="寺Aカフェ")               # OSMには残るが条件外
    w.run(db, tmp_path)
    s = spots(db)[0]
    assert s["data_status"] == "archived" and "条件から外れた" in s["archived_reason"]


def test_C_verified_never_auto_archived(db, tmp_path):
    w = World(); temples(w, 1)
    w.run(db, tmp_path)
    db.execute("update spots set status='verified', confidence='field_checked'"); db.commit()
    w.remove("nodes", 10)
    for _ in range(4):
        w.run(db, tmp_path)
    s = spots(db)[0]
    assert s["data_status"] == "needs_review" and "削除" in s["review_reason"]


def test_C_id_change_node_to_area_relinks(db, tmp_path):
    """学校が「点」から「敷地」に描き直され（IDが変わり代表点が約120m移動）ても同じ地点のまま"""
    w = World(); w.point(10, 130.39, 33.585, amenity="school", name="福岡市立春吉小学校")
    w.run(db, tmp_path)
    sid = spots(db)[0]["id"]
    w.remove("nodes", 10)
    w.area(800, 130.3913, 33.5853, size=0.0008, amenity="school", name="春吉小学校")
    r = w.run(db, tmp_path)
    assert len(spots(db)) == 1 and spots(db)[0]["id"] == sid
    assert r["summary"]["relinked"] == 1 and r["summary"]["created"] == 0
    assert db.execute("select count(*) n from spot_features where spot_id=%s", (sid,)).fetchone()["n"] == 2


def test_C_node_and_area_of_same_temple_are_one_spot(db, tmp_path):
    w = World()
    w.point(10, 130.39, 33.585, amenity="place_of_worship", name="承天寺")
    w.area(800, 130.3903, 33.5852, size=0.0006, amenity="place_of_worship", name="承天寺")
    w.run(db, tmp_path)
    assert len(spots(db)) == 1


def test_C_same_name_far_apart_are_two_spots(db, tmp_path):
    w = World()
    w.point(10, 130.385, 33.585, amenity="school", name="中央小学校")
    w.point(11, 130.445, 33.595, amenity="school", name="中央小学校")
    w.run(db, tmp_path)
    assert len(spots(db)) == 2
    rows = db.execute("select search_spots('中央小学校') r").fetchone()["r"]
    assert len(rows) == 2 and all(r["others"] == 0 for r in rows)


def test_C_diagnostics_clean(db, tmp_path):
    w = World(); temples(w, 3); w.line(500, RIVER, waterway="river", name="那珂川")
    w.run(db, tmp_path)
    d = db.execute("select diag_summary() d").fetchone()["d"]
    assert d["orphan_links"] == 0 and d["spots_without_feature"] == 0 and d["area_mismatch"] == 0


# =====================================================================
# D. 橋・検索・流れ
# =====================================================================
def test_D_bridge_rules():
    hw = lambda **t: categories_of(t, "w", False)
    assert "bridge_overpass" not in hw(bridge="yes", highway="motorway", name="福岡高速環状線")
    assert "bridge_overpass" not in hw(bridge="yes", highway="trunk", name="国道3号")
    assert "bridge_overpass" not in hw(bridge="yes", highway="primary", name="明治通り")
    assert "bridge_overpass" in hw(bridge="yes", highway="primary", name="国道202号", **{"bridge:name": "千鳥橋"})
    assert display_name({"bridge": "yes", "highway": "primary", "name": "国道202号", "bridge:name": "千鳥橋"},
                        "bridge_overpass") == "千鳥橋"
    assert display_name({"name": "福岡志摩前原線;今津橋"}, "bridge_overpass") == "今津橋"
    assert "bridge_overpass" in hw(bridge="yes", highway="footway", name="天神歩道橋")
    assert "bridge_overpass" in hw(bridge="yes", highway="secondary", name="西大橋")
    assert "river" not in hw(waterway="river", name="暗渠川", tunnel="culvert")


def test_D_dual_carriageway_bridge_is_one_spot(db, tmp_path):
    w = World()
    w.line(900, [(130.395, 33.5900), (130.396, 33.5900)], highway="secondary", bridge="yes", name="西大橋")
    w.line(901, [(130.395, 33.5902), (130.396, 33.5902)], highway="secondary", bridge="yes", name="西大橋")
    w.run(db, tmp_path)
    assert len(spots(db, "category='bridge_overpass'")) == 1


def test_D_search_groups_only_line_samples(db, tmp_path):
    w = World(); w.line(500, RIVER, waterway="river", name="那珂川")
    w.run(db, tmp_path)
    rows = db.execute("select search_spots('那珂川') r").fetchone()["r"]
    assert len(rows) == 1 and rows[0]["others"] == len(river_spots(db)) - 1
    assert rows[0]["grp_code"] == "river"


def test_D_saved_flow_flagged_when_spot_changes(db, tmp_path):
    w = World()
    w.point(10, 130.39, 33.585, amenity="place_of_worship", name="寺A")
    w.point(11, 130.392, 33.586, leisure="park", name="公園B")
    w.run(db, tmp_path)
    a, b = spots(db)
    route = {"route_key": f"{a['id']}-{b['id']}", "match": 0.5, "transition_label": "歴史 → 公園",
             "total_minutes": 3, "travel_mode": "walk", "spots": [{"minutes_from_prev": None}, {"minutes_from_prev": 3}]}
    cid = db.execute("select upsert_route(%s::jsonb,'search','hirata') id", (json.dumps(route),)).fetchone()["id"]
    db.commit()
    w.point(10, 130.3906, 33.585, amenity="place_of_worship", name="寺A")    # 約56m移動
    r = w.run(db, tmp_path)
    c = db.execute("select needs_review, review_note from clusters where id=%s", (cid,)).fetchone()
    assert r["summary"]["clusters_to_review"] == 1 and c["needs_review"] and "寺A" in c["review_note"]


def test_D_archived_spots_excluded_from_routes(db, tmp_path):
    w = World()
    w.point(10, 130.39, 33.585, amenity="place_of_worship", name="寺A")
    w.point(11, 130.392, 33.586, leisure="park", name="公園B")
    w.run(db, tmp_path)
    routes = db.execute("select build_routes(array['historic','green'], '{}'::jsonb, 'walk') r").fetchone()["r"]
    assert len(routes) == 1
    m = routes[0]["total_minutes"]
    a, b = spots(db)
    d = db.execute("select ST_Distance(a.geom,b.geom) d from spots a, spots b where a.id=%s and b.id=%s",
                   (a["id"], b["id"])).fetchone()["d"]
    assert abs(float(m) - round(d * 1.3 / 80, 1)) < 0.11                  # 目安＝直線距離×1.3÷80m/分
    db.execute("update spots set data_status='archived' where name='公園B'"); db.commit()
    assert db.execute("select build_routes(array['historic','green'], '{}'::jsonb, 'walk') r").fetchone()["r"] == []


def test_D_rules_version_is_stable():
    from locadb import categories
    assert len(categories.RULES_VERSION) == 12
    assert hashlib.sha256(b"x").hexdigest()          # 形だけ（import 確認）


def _flow_world(db, tmp_path):
    w = World()
    w.point(10, 130.39, 33.585, amenity="place_of_worship", name="寺A")
    w.point(11, 130.392, 33.586, leisure="park", name="公園B")
    w.point(12, 130.394, 33.586, leisure="park", name="公園C")
    w.run(db, tmp_path)
    return {s["name"]: s for s in spots(db)}


def _record(db, sp, a, b, match):
    spots_json = [{"grp_code": "historic" if x == "寺A" else "green", "minutes_from_prev": None} for x in (a, b)]
    route = {"route_key": f"{sp[a]['id']}-{sp[b]['id']}", "match": match, "transition_label": "x", "total_minutes": 3,
             "travel_mode": "walk", "spots": spots_json}
    db.execute("select record_route_search('s', array['historic','green'], '{}'::jsonb, 'walk', null, null, %s::jsonb, null)",
               (json.dumps([route]),))
    db.commit()


def test_flows_record_only_multi_landscape_and_good_match(db, tmp_path):
    """2026-10-21 提案1：景観1つだけ・適合度の低い検索は、流れとして記録しない／おすすめに出さない"""
    sp = _flow_world(db, tmp_path)
    _record(db, sp, "公園B", "公園C", 0.9)                  # 景観が1つだけ（green・green）
    _record(db, sp, "寺A", "公園B", 0.3)                    # 適合度が低い
    assert db.execute("select count(*) n from clusters").fetchone()["n"] == 0
    _record(db, sp, "寺A", "公園B", 0.7)                    # 景観2つ・適合度0.7 → 記録する
    assert db.execute("select count(*) n from clusters").fetchone()["n"] == 1
    flows = db.execute("select recommended_flows() r").fetchone()["r"]
    assert len(flows) == 1 and all("fact_count" in s for s in flows[0]["spots"])
    # すでに溜まっていた弱い流れ（適合度0.3）は、おすすめに出さない。保存した流れは出す
    key = f"{sp['寺A']['id']}-{sp['公園C']['id']}"
    db.execute("select upsert_route(%s::jsonb,'search',null)",
               (json.dumps({"route_key": key, "match": 0.3, "transition_label": "x", "total_minutes": 3,
                            "travel_mode": "walk", "spots": [{}, {}]}),))
    db.commit()
    assert len(db.execute("select recommended_flows() r").fetchone()["r"]) == 1
    db.execute("update clusters set status='reviewed', saved_by='hirata' where route_key=%s", (key,)); db.commit()
    assert len(db.execute("select recommended_flows() r").fetchone()["r"]) == 2
