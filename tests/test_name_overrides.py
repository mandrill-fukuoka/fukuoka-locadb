from locadb import name_overrides as no
from tools import name_ja_import as imp
from osmworld import World, spots, spot

OSC = """<?xml version="1.0" encoding="UTF-8"?>
<osmChange version="0.6"><modify>
<node id="10" lat="33.585" lon="130.39" version="1" changeset="0"><tag k="amenity" v="cafe"/><tag k="name" v="REC COFFEE"/><tag k="name:ja" v="レックコーヒー"/></node>
<node id="11" lat="33.590" lon="130.40" version="1" changeset="0"><tag k="amenity" v="cafe"/><tag k="name" v="Acrosquare"/><tag k="name:ja" v="アクロス福岡（要候補照合）"/></node>
<node id="99" lat="33.6" lon="130.4" version="1" changeset="0"><tag k="name" v="No Spot"/><tag k="name:ja" v="地点なし"/></node>
<way id="12" version="1" changeset="0"><nd ref="1"/><tag k="name" v="W"/><tag k="name:ja" v="ウェイ"/></way>
<node id="13" lat="33.6" lon="130.4" version="1" changeset="0"><tag k="name" v="No ja"/></node>
</modify></osmChange>"""


def test_parse_osc_skips_pending_marker_and_untagged():
    ok, skipped = no.parse_osc(OSC)
    assert {o["key"]: o["name_ja"] for o in ok} == {"n10": "レックコーヒー", "n99": "地点なし", "w12": "ウェイ"}
    assert [s["key"] for s in skipped] == ["n11"]


def test_import_renames_keeps_old_name_and_survives_sync(db, tmp_path):
    w = World()
    w.point(10, 130.39, 33.585, amenity="cafe", name="REC COFFEE")
    w.point(11, 130.40, 33.590, amenity="cafe", name="Acrosquare")
    w.run(db, tmp_path)
    f = tmp_path / "x.osc"; f.write_text(OSC, encoding="utf-8")
    dry = imp.run(db, f, apply=False)
    assert dry["renamed"] == 1 and [x["key"] for x in dry["no_spot"]] == ["n99", "w12"]
    assert spot(db, name="REC COFFEE") is not None                              # 試しでは変えない
    res = imp.run(db, f, apply=True)
    assert res["renamed"] == 1
    s = spot(db, name="レックコーヒー")
    assert s is not None and "REC COFFEE" in s["alt_names"]                    # 旧名は別名に残る
    assert spot(db, name="Acrosquare") is not None                              # 確認の途中の印があるものは変えない
    assert db.execute("select event, actor from spot_history where spot_id=%s order by id desc limit 1", (s["id"],)).fetchone() \
        == {"event": "renamed", "actor": "name-ja-import"}
    assert imp.run(db, f, apply=True)["renamed"] == 0 and imp.run(db, f, apply=True)["same"] == 1      # 二度実行しても同じ
    # 自動収集（OSM には name:ja が無いまま）をしても、名前は戻らない
    w.run(db, tmp_path)
    assert spot(db, id=s["id"])["name"] == "レックコーヒー"
    # OSM に name:ja が付いたら、そちらが優先
    w2 = World()
    w2.point(10, 130.39, 33.585, amenity="cafe", name="REC COFFEE", **{"name:ja": "REC COFFEE 天神"})
    w2.point(11, 130.40, 33.590, amenity="cafe", name="Acrosquare")
    w2.run(db, tmp_path)
    assert spot(db, id=s["id"])["name"] == "REC COFFEE 天神"
