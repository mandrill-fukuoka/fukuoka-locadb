"""重要度の点数と並び順（順2、DB変更260）の回帰テスト"""
import json

from test_routes import add
from test_search_sort import search, names


def signal(db, sid, kind, value):
    db.execute("insert into spot_signals (spot_id, kind, value) values (%s, %s, %s)", (sid, kind, value))
    db.commit()


def refresh(db):
    n = db.execute("select refresh_spot_importance() as n").fetchone()["n"]
    db.commit()
    return n


def score(db, sid):
    r = db.execute("select score, hidden_score, parts from spot_importance where spot_id = %s", (sid,)).fetchone()
    return r


def make(db):
    """有名（閲覧数・言語版が多い）／材料が豊富な穴場（事実・写真候補）／材料なし／名前なし"""
    famous = add(db, "有名岸", "coast", 0, 0, address="福岡市中央区あ")
    hidden = add(db, "穴場岸", "coast", 300, 0, address="福岡市中央区い")
    bare = add(db, "素の岸", "coast", 600, 0, address="福岡市中央区う")
    unnamed = add(db, "（無名）岸", "coast", 900, 0, address="福岡市中央区え")
    signal(db, famous, "wikipedia_views", 150000)
    signal(db, famous, "wikidata_sitelinks", 35)
    signal(db, unnamed, "wikipedia_views", 150000)
    for i in range(3):
        db.execute("insert into spot_facts (spot_id, item, value, source_kind, source_url, approved_by) "
                   "values (%s, '規模', %s, 'osm', 'https://example.com/x', 't')", (hidden, f"v{i}"))
    for i in range(10):
        db.execute("insert into spot_photo_candidates (spot_id, file_title, page_url, image_url, license) "
                   "values (%s, %s, 'https://example.com/p', 'https://example.com/i', 'CC0')", (hidden, f"File:{i}.jpg"))
    db.commit()
    refresh(db)
    return famous, hidden, bare, unnamed


def test_refresh_scores_parts_and_skips_unnamed(db):
    famous, hidden, bare, unnamed = make(db)
    f, h, b = score(db, famous), score(db, hidden), score(db, bare)
    assert score(db, unnamed) is None                      # 名前のない地点は対象外
    assert f["score"] > h["score"] > b["score"] == 0       # 知名度＞材料のみ＞材料なし
    assert float(f["parts"]["wiki_views"]) > 0.9 and float(f["parts"]["sitelinks"]) > 0.8
    assert h["hidden_score"] > f["hidden_score"] and b["hidden_score"] == 0   # 穴場度：材料が豊富で知名度が低いほど高い
    assert refresh(db) == 3                                 # 何度実行しても同じ（作り直し）


def test_default_order_uses_importance_then_unnamed_and_bare_last(db):
    famous, hidden, bare, unnamed = make(db)
    rows = search(db, p_category="coast")
    assert names(rows) == ["有名岸", "穴場岸", "素の岸", "（無名）岸"]
    assert [r["total"] for r in rows] == [4] * 4


def test_importance_and_hidden_sorts(db):
    make(db)
    assert names(search(db, p_category="coast", p_sort="importance"))[:2] == ["有名岸", "穴場岸"]
    hid = names(search(db, p_category="coast", p_sort="hidden"))
    assert hid[0] == "穴場岸" and hid[-1] == "（無名）岸"   # 穴場順：材料が豊富な低知名度が先頭、名前なしは最後
    assert names(search(db, p_category="coast", p_sort="nonsense"))[0] == "有名岸"   # 未知の値は既定順


def test_name_similarity_still_comes_before_importance(db):
    make(db)
    add(db, "有名な滝", "coast", 1200, 0, address="福岡市中央区お")
    rows = search(db, p_q="有名岸")
    assert names(rows)[0] == "有名岸"


def test_facts_import_accepts_wikidata_and_revoke_by_source(db):
    sid = add(db, "古い社", "coast", 0, 0, address="福岡市中央区あ")
    item = {"spot_id": sid, "item": "創設・開設年", "value": "1950年", "source_kind": "wikidata",
            "source_url": "https://www.wikidata.org/wiki/Q1", "source_title": "Wikidata Q1", "license": "CC0 1.0"}
    r = db.execute("select facts_import(%s::jsonb, 'import:wikidata') as r", (json.dumps([item], ensure_ascii=False),)).fetchone()["r"]
    assert r["added"] == 1 and not r["rejected"]
    r = db.execute("select facts_import(%s::jsonb, 'import:wikidata') as r", (json.dumps([item], ensure_ascii=False),)).fetchone()["r"]
    assert r["skipped"] == 1                                  # 何度実行しても同じ
    assert db.execute("select revoke_facts_by_source('wikidata', 'user', 'テスト') as n").fetchone()["n"] == 1
    st = db.execute("select status, revoked_by from spot_facts where spot_id = %s", (sid,)).fetchone()
    assert st["status"] == "revoked" and st["revoked_by"] == "user"
    assert db.execute("select revoke_facts_by_source('wikidata', 'user') as n").fetchone()["n"] == 0
