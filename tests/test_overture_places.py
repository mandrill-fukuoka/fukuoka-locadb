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
    assert db.execute("select vibe_version() v").fetchone()["v"] == "vibe-v3"
    st = db.execute("select density_stats() d").fetchone()["d"]
    assert st["points"] == 30 and st["by_cls"]["bar"] == 8


def test_replace_points_is_atomic(db):
    rows = [("a", "bar", "bar", 0.9, 130.4, 33.59), ("b", "eat", "cafe", 0.8, 130.41, 33.59)]
    assert op.replace_points(db, rows, "2026-11-19.0") == 2
    db.commit()
    assert db.execute("select count(*) n from place_points where release = '2026-11-19.0'").fetchone()["n"] == 2
    assert op.replace_points(db, rows[:1], "2026-12-17.0") == 1
    db.commit()
    assert db.execute("select count(*) n from place_points").fetchone()["n"] == 1
