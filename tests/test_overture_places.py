"""Overture の店の密度：分類（DBなし）と、取り込み・数え直し・vibe への反映（DB。ci）"""
import pytest

from locadb import overture_places as op


def test_classify():
    assert op.classify("bar", "bar") == "bar"
    assert op.classify("restaurant", "sake_bar") == "bar"            # 種別が酒場なら夜の店
    assert op.classify("restaurant", "japanese_restaurant") == "eat"
    assert op.classify("cafe", "cafe") == "eat" and op.classify(None, "bakery") == "eat"
    assert op.classify("fashion_and_apparel_store", "clothing_store") == "retail"
    assert op.classify("convenience_store", "convenience_store") == "retail"
    assert op.classify("dental_clinic", "dental_clinic") == "other"
    assert op.classify("atm", "atm") is None and op.classify("parking", None) is None
    assert op.classify(None, None) == "other"


class FakeCon:
    def __init__(self, rows, cols):
        self.rows, self.cols = rows, cols

    def execute(self, sql, *a):
        self.last = sql
        return self

    def fetchall(self):
        if self.last.lstrip().startswith("describe"):
            return [(c, "VARCHAR") for c in self.cols]
        return self.rows


def test_fetch_filters_closed_and_skipped():
    rows = [("a", "bar", "bar", 0.9, "open", 130.4, 33.59),
            ("b", "restaurant", "japanese_restaurant", 0.8, "permanently_closed", 130.4, 33.59),
            ("c", "atm", "atm", 0.9, None, 130.4, 33.59),
            ("d", "dental_clinic", "dental_clinic", 0.7, None, 130.4, 33.59)]
    out, rel = op.fetch("2026-11-19.0", con=FakeCon(rows, ["id", "basic_category", "taxonomy", "operating_status", "bbox", "confidence"]))
    assert [r[0] for r in out] == ["a", "d"] and rel == "2026-11-19.0"
    assert out[0][1] == "bar" and out[1][1] == "other"


# ---- 以下は DB（ci のみ）----
from test_routes import add, at, spatial  # noqa: E402


def put(db, cls, dx, dy, n=1):
    lon, lat = at(dx, dy)
    for i in range(n):
        db.execute("insert into place_points (gers_id, cls, cat, conf, geom, release) values (%s, %s, 'x', 0.9, "
                   "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 'test')", (f"g{dx}-{dy}-{cls}-{i}", cls, lon, lat))
    db.commit()


def test_density_refresh_counts_and_vibe_uses_it(db):
    sid = add(db, "天神の通り", "alley", 0, 0)
    spatial(db, sid, road_width_m=3.0)
    far = add(db, "山の中", "alley", 20000, 0)
    spatial(db, far, road_width_m=3.0)
    assert db.execute("select density_refresh() n").fetchone()["n"] == 0          # 点がなければ何もしない
    put(db, "bar", 20, 0, 5)
    put(db, "eat", 0, 60, 8)
    put(db, "retail", 100, 0, 4)
    put(db, "other", 0, 300, 10)                                                  # 150m の外・500m の内
    put(db, "bar", 0, 800, 3)                                                     # 500m の外
    assert db.execute("select density_refresh() n").fetchone()["n"] == 2
    d = db.execute("select * from spot_density where spot_id = %s", [sid]).fetchone()
    assert (d["bar_150"], d["eat_150"], d["shop_150"], d["any_150"], d["any_500"]) == (5, 8, 17, 17, 27)
    f = db.execute("select * from spot_density where spot_id = %s", [far]).fetchone()
    assert f["shop_150"] == 0 and f["any_500"] == 0
    db.execute("select vibe_apply()")
    db.commit()
    r = db.execute("select bars_150, eateries_150, shops_150 from spot_spatial where spot_id = %s", [sid]).fetchone()
    assert (r["bars_150"], r["eateries_150"], r["shops_150"]) == (5, 8, 17)
    assert db.execute("select vibe_version() v").fetchone()["v"] == "vibe-v5"
    st = db.execute("select density_stats() d").fetchone()["d"]
    assert st["points"] == 30 and st["by_cls"]["bar"] == 8


def test_replace_points_is_atomic(db):
    rows = [("a", "bar", "bar", 0.9, 130.4, 33.59), ("b", "eat", "cafe", 0.8, 130.41, 33.59)]
    assert op.replace_points(db, rows, "2026-11-19.0")["points"] == 2
    db.commit()
    assert db.execute("select count(*) n from place_points where release = '2026-11-19.0'").fetchone()["n"] == 2
    assert op.replace_points(db, rows[:1], "2026-12-17.0")["points"] == 1
    db.commit()
    assert db.execute("select count(*) n from place_points").fetchone()["n"] == 1


def test_replace_points_records_gone(db):
    rows = [(f"g{i}", "eat", "cafe", 0.9, 130.4 + i * 0.0001, 33.59) for i in range(10)]
    assert op.replace_points(db, rows, "r1")["gone"] == 0                     # 初回は前がないので記録しない
    out = op.replace_points(db, rows[:8], "r2")                               # 2/10 が消えた（上限の3割以内）
    assert out["gone"] == 2
    gone = db.execute("select gers_id, release_from, release_to from place_points_gone order by gers_id").fetchall()
    assert [g["gers_id"] for g in gone] == ["g8", "g9"] and gone[0]["release_from"] == "r1" and gone[0]["release_to"] == "r2"
    assert op.replace_points(db, rows[:8], "r2")["gone"] == 0                 # 同じリリースの再実行では何も残さない
    assert db.execute("select count(*) n from place_points_gone").fetchone()["n"] == 0
    out = op.replace_points(db, rows[:3], "r3")                               # 5/8 が一度に消えた → ID の振り直しとみる
    assert out["gone"] == 0 and "振り直し" in out["gone_note"]
    assert db.execute("select count(*) n from place_points_gone").fetchone()["n"] == 0


def gone(db, dx, dy, n):
    lon, lat = at(dx, dy)
    for i in range(n):
        db.execute("insert into place_points_gone (gers_id, cls, cat, geom, release_from, release_to) values "
                   "(%s, 'eat', 'x', ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 'r1', 'r2')",
                   (f"x{dx}-{dy}-{i}", lon, lat))
    db.commit()


def test_overture_flags_need_many_and_most_gone(db):
    sid = add(db, "旧天神ビル", "new_building", 0, 0)
    few = add(db, "港の倉庫群", "new_building", 5000, 0)
    mixed = add(db, "駅前ビル", "new_building", 0, 5000)
    infra = add(db, "那珂川の橋", "bridge_overpass", 0, 10000)
    gone(db, 10, 0, 4)                       # 4 件消えて 0 件残る → 出す
    gone(db, 5000, 10, 2)                    # 2 件だけ → 出さない
    gone(db, 10, 5000, 3)
    put(db, "eat", 10, 5000, 5)              # 3 件消えて 5 件残る（37%）→ 出さない
    gone(db, 10, 10000, 5)                   # 橋は照合しない
    r = db.execute("select change_overture_flags() r").fetchone()["r"]
    db.commit()
    assert r["flagged"] == 1 and r["gone"] == 14
    rk = db.execute("select spot_id, risk_type, source_kind, verified, certainty from change_risks").fetchall()
    assert [(x["spot_id"], x["risk_type"], x["source_kind"], x["verified"], x["certainty"]) for x in rk] == \
        [(sid, "closure", "overture", False, "keyword")]
    assert db.execute("select change_overture_flags() r").fetchone()["r"]["flagged"] == 0     # 同じリリースで二重に出さない
    assert few and mixed and infra


def link(db, spot_id, key, tags, osm_type="w", osm_id=1):
    import json
    db.execute("insert into features (key, kind, osm_type, osm_id, tags) values (%s,'element',%s,%s,%s::jsonb) "
               "on conflict (key) do update set tags = excluded.tags", (key, osm_type, osm_id, json.dumps(tags)))
    db.execute("insert into spot_features (spot_id, feature_key) values (%s,%s) on conflict do nothing", (spot_id, key))
    db.commit()


def test_osm_marks_baseline_then_new_marks(db):
    a = add(db, "大名の古い店", "new_building", 0, 0)
    b = add(db, "渡辺通りの建物", "new_building", 500, 0)
    c = add(db, "那珂川の橋", "bridge_overpass", 1000, 0)
    link(db, a, "w1", {"shop": "bar", "disused:shop": "bar"}, osm_id=1)          # 最初から閉鎖の印あり
    link(db, b, "w2", {"building": "yes"}, osm_id=2)
    link(db, c, "w3", {"bridge": "yes"}, osm_id=3)
    r = db.execute("select change_osm_marks_refresh() r").fetchone()["r"]
    db.commit()
    assert r == {"baseline": True, "features_marked": 1, "new_marks": 0, "flagged": 0}      # 初回は基準を覚えるだけ
    assert db.execute("select count(*) n from change_risks").fetchone()["n"] == 0
    link(db, b, "w2", {"building": "construction"}, osm_id=2)                  # 工事中になった
    link(db, c, "w3", {"bridge": "yes", "demolished:bridge": "yes"}, osm_id=3)  # 橋は照合しない
    link(db, a, "w1", {"shop": "bar", "disused:shop": "bar", "demolished:building": "yes"}, osm_id=1)   # 解体の印が増えた
    r = db.execute("select change_osm_marks_refresh() r").fetchone()["r"]
    db.commit()
    assert r["baseline"] is False and r["flagged"] == 2
    rows = {(x["spot_id"], x["risk_type"]): x for x in db.execute("select * from change_risks").fetchall()}
    assert set(rows) == {(b, "redevelopment"), (a, "demolition")}
    x = rows[(b, "redevelopment")]
    assert x["source_kind"] == "osm_tags" and not x["verified"] and x["source_url"] == "https://www.openstreetmap.org/way/2"
    r = db.execute("select change_osm_marks_refresh() r").fetchone()["r"]       # 変化がなければ何も出さない
    assert r["flagged"] == 0 and r["new_marks"] == 0
