"""検索エンジンに載せる地点の条件とサイトマップ（追補1）"""
from test_routes import add


def make(db):
    ok_summary = add(db, "説明あり", "coast", 0, 0)
    db.execute("update spots set summary = '朝の港。', summary_by = 'tester' where id = %s", (ok_summary,))
    ok_fact = add(db, "事実あり", "coast", 300, 0)
    db.execute("insert into spot_facts (spot_id, item, value, source_kind, source_url, approved_by) "
               "values (%s, '規模', '1ha', 'osm', 'https://example.com/x', 't')", (ok_fact,))
    ok_ffc = add(db, "FFCあり", "coast", 600, 0, ffc_registered=True)
    thin = add(db, "材料なし", "coast", 900, 0)
    unnamed = add(db, "（無名）の海岸", "coast", 1200, 0, ffc_registered=True)
    review = add(db, "確認待ち", "coast", 1500, 0, ffc_registered=True, data_status="needs_review")
    gone = add(db, "対象外", "coast", 1800, 0, ffc_registered=True, data_status="archived")
    db.commit()
    return dict(ok_summary=ok_summary, ok_fact=ok_fact, ok_ffc=ok_ffc, thin=thin, unnamed=unnamed, review=review, gone=gone)


def test_indexable_and_sitemap_agree(db):
    ids = make(db)
    flags = {k: db.execute("select spot_is_indexable(%s) as r", (v,)).fetchone()["r"] for k, v in ids.items()}
    assert flags == dict(ok_summary=True, ok_fact=True, ok_ffc=True, thin=False, unnamed=False, review=False, gone=False)
    sm = db.execute("select sitemap_spots(0, 100) as r").fetchone()["r"]
    assert sorted(x["id"] for x in sm) == sorted([ids["ok_summary"], ids["ok_fact"], ids["ok_ffc"]])
    assert db.execute("select sitemap_count() as n").fetchone()["n"] == 3
    assert len(db.execute("select sitemap_spots(1, 1) as r").fetchone()["r"]) == 1          # ページ送り
    d = db.execute("select spot_detail(%s) as r", (ids["thin"],)).fetchone()["r"]
    assert d["indexable"] is False
    assert db.execute("select spot_detail(%s) as r", (ids["ok_fact"],)).fetchone()["r"]["indexable"] is True
