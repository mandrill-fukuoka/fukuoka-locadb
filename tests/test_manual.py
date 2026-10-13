"""回帰テスト：地点の手入力（タスクA）

- 手入力・人が直した地点の関数（create_manual_spot / edit_spot / manual_check / save_practicals）
- 保護された地点は「前回見たOSMの値」と比べる（要確認を解消した地点・人が直した地点が毎回ぶり返さない）
"""
import json

import psycopg
import pytest

from osmworld import World, events, spot, spots


def rpc(db, fn, **kw):
    args = ", ".join(f"{k} => %({k})s" for k in kw)
    row = db.execute(f"select {fn}({args}) r", kw).fetchone()
    db.commit()
    return row["r"]


def temple(w, lon=130.39, name="警固神社", nid=10):
    w.point(nid, lon, 33.585, amenity="place_of_worship", name=name)


def resolve(db, sid):
    db.execute("select set_spot_state(%s, 'resolve', 'tester')", (sid,)); db.commit()


# =====================================================================
# 手入力の関数
# =====================================================================
def test_create_manual_spot_codes_area_and_history(db, tmp_path):
    w = World(); w.run(db, tmp_path)                       # 区の境界だけ読み込む
    a = rpc(db, "create_manual_spot", p_name="承天寺", p_lat=33.5935, p_lon=130.4135,
            p_category="shrine_temple", p_source_url="https://example.jp/jotenji", p_user="hirata")
    b = rpc(db, "create_manual_spot", p_name="承天寺 通り沿いの門", p_lat=33.5936, p_lon=130.4136,
            p_category="heritage", p_user="hirata")
    c = rpc(db, "create_manual_spot", p_name="博多の路地", p_lat=33.59, p_lon=130.43,
            p_category="alley", p_user="hirata")
    assert (a["code"], b["code"], c["code"]) == ("FUK-CHU-M0001", "FUK-CHU-M0002", "FUK-HAK-M0001")
    s = spot(db, id=a["id"])
    assert s["origin"] == "manual" and s["position_kind"] == "manual" and s["confidence"] == "unverified"
    assert s["area_code"] == "fuk-chuo" and s["status"] == "candidate"
    h = db.execute("select actor, event, detail from spot_history where spot_id=%s", (a["id"],)).fetchone()
    assert h["actor"] == "hirata" and h["event"] == "created" and h["detail"]["manual"] is True
    src = db.execute("select kind, url from sources where spot_id=%s", (a["id"],)).fetchone()
    assert src["url"] == "https://example.jp/jotenji"
    d = rpc(db, "spot_detail", p_id=a["id"])
    assert d["origin"] == "manual" and d["created_by"] == "hirata"


def test_create_manual_spot_rejects_outside_and_bad_input(db, tmp_path):
    w = World(); w.run(db, tmp_path)
    for kw, msg in [(dict(p_lat=33.70, p_lon=130.40), "対象エリア"),
                    (dict(p_name=" ", p_lat=33.59, p_lon=130.40), "名前"),
                    (dict(p_category="nope", p_lat=33.59, p_lon=130.40), "種別")]:
        args = dict(p_name="テスト", p_category="park", p_user="u") | kw
        with pytest.raises(psycopg.errors.RaiseException, match=msg):
            rpc(db, "create_manual_spot", **args)
        db.rollback()
    assert spots(db) == []


def test_manual_check_finds_similar_nearby(db, tmp_path):
    w = World(); temple(w, 130.39, "承天寺"); w.run(db, tmp_path)
    r = rpc(db, "manual_check", p_lat=33.5852, p_lon=130.3901, p_name="承天寺")
    assert r["area_code"] == "fuk-chuo"
    assert r["nearby"][0]["name"] == "承天寺" and r["nearby"][0]["similar"] is True
    assert rpc(db, "manual_check", p_lat=33.70, p_lon=130.40)["area_code"] is None


def test_manual_spot_untouched_and_osm_arrival_makes_no_duplicate(db, tmp_path):
    """手入力の地点は収集で動かない・消えない。後からOSMに同じ名前の要素が現れても地点は増えない"""
    w = World(); w.run(db, tmp_path)
    m = rpc(db, "create_manual_spot", p_name="承天寺", p_lat=33.585, p_lon=130.39, p_category="shrine_temple")
    for _ in range(2):
        w.run(db, tmp_path)
    assert spot(db, id=m["id"])["data_status"] == "current" and events(db, m["id"]) == ["created"]
    temple(w, 130.3902, "承天寺")                              # 約19m東にOSMの要素
    w.run(db, tmp_path); w.run(db, tmp_path)
    live = spots(db, "data_status <> 'archived'")
    assert len(live) == 1 and live[0]["id"] == m["id"] and live[0]["origin"] == "manual"
    assert abs(live[0]["lon"] - 130.39) < 1e-9               # 位置は人が置いたまま


def test_edit_osm_spot_locks_and_is_not_flagged_next_run(db, tmp_path):
    """人が直したOSMの地点：次の収集で元に戻らず、要確認にもならない。OSMが変わったときだけ要確認"""
    w = World(); temple(w); w.run(db, tmp_path)
    s = spot(db, name="警固神社")
    r = rpc(db, "edit_spot", p_id=s["id"], p_name="警固神社（拝殿前）", p_lat=33.5853, p_lon=130.3904,
            p_user="hirata", p_note="撮影位置")
    assert set(r["changed"]) == {"name", "position"}
    s2 = spot(db, id=s["id"])
    assert s2["locked"] and s2["position_kind"] == "manual" and "警固神社" in s2["alt_names"]
    assert s2["osm_seen"]["name"] == "警固神社"
    for _ in range(2):
        w.run(db, tmp_path)
    s3 = spot(db, id=s["id"])
    assert s3["data_status"] == "current" and s3["name"] == "警固神社（拝殿前）"
    assert abs(s3["lon"] - 130.3904) < 1e-9
    assert "needs_review" not in events(db, s["id"])
    temple(w, name="警固神社 本殿")                           # OSMで名前が変わった
    w.run(db, tmp_path)
    s4 = spot(db, id=s["id"])
    assert s4["data_status"] == "needs_review" and s4["name"] == "警固神社（拝殿前）"
    h = db.execute("select detail from spot_history where spot_id=%s and event='needs_review'", (s["id"],)).fetchone()
    assert h["detail"]["proposed"]["renamed"] == {"from": "警固神社", "to": "警固神社 本殿"}


def test_edit_spot_area_change_keeps_code(db, tmp_path):
    w = World(); w.run(db, tmp_path)
    m = rpc(db, "create_manual_spot", p_name="境の橋", p_lat=33.59, p_lon=130.418, p_category="bridge_overpass")
    rpc(db, "edit_spot", p_id=m["id"], p_lat=33.59, p_lon=130.422, p_user="u")
    s = spot(db, id=m["id"])
    assert s["code"] == "FUK-CHU-M0001" and s["area_code"] == "fuk-hakata"
    assert events(db, m["id"]) == ["created", "moved", "area"]
    with pytest.raises(psycopg.errors.RaiseException, match="対象エリア"):
        rpc(db, "edit_spot", p_id=m["id"], p_lat=33.70, p_lon=130.40)


def test_save_practicals(db, tmp_path):
    w = World(); temple(w); w.run(db, tmp_path)
    sid = spot(db, name="警固神社")["id"]
    rpc(db, "save_practicals", p_id=sid, p_permit_required="consult", p_permit_contact="社務所",
        p_restrictions="参拝者の妨げにならないこと", p_past_productions=json.dumps(["映画A（2024）"]),
        p_notes=None, p_user="hirata")
    d = rpc(db, "spot_detail", p_id=sid)
    assert d["practical"]["permit_contact"] == "社務所" and d["practical"]["updated_by"] == "hirata"
    assert d["practical"]["past_productions"] == ["映画A（2024）"]
    assert spot(db, id=sid)["locked"] is False                # 撮影実務の入力では位置は固定しない
    w.run(db, tmp_path)
    assert rpc(db, "spot_detail", p_id=sid)["practical"]["permit_contact"] == "社務所"


# =====================================================================
# 保護された地点の要確認がぶり返さない（sync.py）
# =====================================================================
def test_resolved_review_does_not_come_back(db, tmp_path):
    w = World(); temple(w); w.run(db, tmp_path)
    s = spot(db, name="警固神社")
    db.execute("update spots set status='verified' where id=%s", (s["id"],)); db.commit()
    temple(w, 130.3910)                                     # OSMで約93m東へ
    w.run(db, tmp_path)
    assert spot(db, id=s["id"])["data_status"] == "needs_review"
    resolve(db, s["id"])                                    # 人が「今の位置のまま」と判断
    w.run(db, tmp_path); w.run(db, tmp_path)
    s2 = spot(db, id=s["id"])
    assert s2["data_status"] == "current" and s2["lon"] == s["lon"]
    assert events(db, s["id"]).count("needs_review") == 1
    temple(w, 130.3920)                                     # OSMがさらに動いた → また要確認
    w.run(db, tmp_path)
    assert spot(db, id=s["id"])["data_status"] == "needs_review"


def test_resolved_missing_does_not_come_back(db, tmp_path):
    w = World(); temple(w); w.run(db, tmp_path)
    sid = spot(db, name="警固神社")["id"]
    db.execute("update spots set confidence='field_checked' where id=%s", (sid,)); db.commit()
    w.remove("nodes", 10)
    w.run(db, tmp_path)
    assert spot(db, id=sid)["data_status"] == "needs_review"
    resolve(db, sid)
    w.run(db, tmp_path); w.run(db, tmp_path)
    assert spot(db, id=sid)["data_status"] == "current"
    assert events(db, sid).count("needs_review") == 1
    temple(w)                                               # OSMに戻っても騒がない
    w.run(db, tmp_path)
    s = spot(db, id=sid)
    assert s["data_status"] == "current" and events(db, sid).count("needs_review") == 1


def test_unprotected_spots_do_not_store_osm_seen(db, tmp_path):
    w = World(); temple(w); w.run(db, tmp_path); w.run(db, tmp_path)
    assert spot(db, name="警固神社")["osm_seen"] is None


def test_review_queue_manual(db, tmp_path):
    w = World(); temple(w); w.run(db, tmp_path)
    m = rpc(db, "create_manual_spot", p_name="承天寺", p_lat=33.59, p_lon=130.41, p_category="shrine_temple")
    rows = rpc(db, "review_queue", p_queue="manual")
    assert [r["id"] for r in rows] == [m["id"]] and rows[0]["origin"] == "manual"
