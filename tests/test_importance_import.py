"""重要度の取り込み（tools/importance_import.py）のテスト。ネットワークは使わない"""
import json
import os
from datetime import date

import psycopg

from test_routes import add
from tools import importance_import as ii


def claim(time, precision=9, rank="normal"):
    return {"rank": rank, "mainsnak": {"datavalue": {"value": {"time": time, "precision": precision}}}}


def test_build_targets_normalizes_and_ignores_bad_tags():
    rows = [(1, "Q10", "ja:福岡タワー"), (2, "Q10", None), (3, "not-q", "en:Fukuoka_Tower"), (4, None, "bad"), (5, " Q7 ", "ja:A_B")]
    by_q, by_title = ii.build_targets(rows)
    assert by_q == {"Q10": [1, 2], "Q7": [5]}
    assert by_title == {("ja", "福岡タワー"): [1], ("en", "Fukuoka Tower"): [3], ("ja", "A B"): [5]}


def test_year_from_claim_needs_one_clear_year():
    assert ii.year_from_claim({"P571": [claim("+1950-00-00T00:00:00Z")]}) == 1950
    assert ii.year_from_claim({"P571": [claim("+1950-03-04T00:00:00Z", 11), claim("+1950-00-00T00:00:00Z")]}) == 1950   # 同じ年なら1つ
    assert ii.year_from_claim({"P571": [claim("+1950-00-00T00:00:00Z"), claim("+1960-00-00T00:00:00Z")]}) is None        # 食い違い
    assert ii.year_from_claim({"P571": [claim("+1950-00-00T00:00:00Z", 8)]}) is None                                      # 十年単位は使わない
    assert ii.year_from_claim({"P571": [claim("+1950-00-00T00:00:00Z", 9, "deprecated")]}) is None
    assert ii.year_from_claim({"P571": [claim("+0100-00-00T00:00:00Z")]}) is None                                         # 古すぎる
    assert ii.year_from_claim({"P571": [claim("-0500-00-00T00:00:00Z")]}) is None
    assert ii.year_from_claim({}) is None


def test_parse_entity_counts_only_wikipedia_editions():
    ent = {"sitelinks": {"jawiki": {"title": "福岡タワー"}, "enwiki": {}, "commonswiki": {}, "wikidatawiki": {}, "jawikivoyage": {}, "dewiki": {}},
           "claims": {"P571": [claim("+1989-00-00T00:00:00Z")]}}
    assert ii.parse_entity(ent) == {"sitelinks": 3, "jawiki": "福岡タワー", "year": 1989}


def test_views_mean_and_month_range():
    assert ii.views_mean([{"views": 120}, {"views": 60}], months=12) == 15.0
    assert ii.month_range(date(2026, 11, 19)) == ("2025110100", "2026100100")
    assert ii.month_range(date(2026, 1, 5)) == ("2025010100", "2025120100")


def test_run_writes_signals_facts_and_scores(db, monkeypatch):
    a = add(db, "福岡タワー", "coast", 0, 0, address="福岡市中央区あ")
    b = add(db, "古い社", "coast", 300, 0, address="福岡市中央区い")
    c = add(db, "他の地点", "coast", 600, 0, address="福岡市中央区う")
    for sid, key, tags in ((a, "n1", {"wikidata": "Q1", "wikipedia": "ja:福岡タワー"}), (b, "n2", {"wikidata": "Q2"}), (c, "n3", {"name": "x"})):
        db.execute("insert into features (key, kind, tags) values (%s, 'element', %s::jsonb)", (key, json.dumps(tags)))
        db.execute("insert into spot_features (spot_id, feature_key) values (%s, %s)", (sid, key))
    db.execute("insert into spot_facts (spot_id, item, value, source_kind, source_url, approved_by) "
               "values (%s, '創設・開設年', '1900年', 'web', 'https://example.com/x', 't')", (b,))
    db.commit()
    monkeypatch.setattr(ii, "fetch_wikidata", lambda q: {"Q1": {"sitelinks": 30, "jawiki": "福岡タワー", "year": 1989},
                                                         "Q2": {"sitelinks": 2, "jawiki": None, "year": 1950}})
    monkeypatch.setattr(ii, "fetch_views", lambda titles, today: {("ja", "福岡タワー"): 90000.0})
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        st = ii.run(conn, today=date(2026, 11, 19))
        conn.commit()
    assert st["qids"] == 2 and st["signals"] == 3 and st["scored"] == 3
    assert st["facts_added"] == 1 and st["facts_skipped_existing"] == 1        # b は承認済みの開設年があるので入れない
    sig = {(r["spot_id"], r["kind"]): r["value"] for r in db.execute("select * from spot_signals").fetchall()}
    assert sig[(a, "wikipedia_views")] == 90000.0 and sig[(a, "wikidata_sitelinks")] == 30 and sig[(b, "wikidata_sitelinks")] == 2
    f = db.execute("select value, source_kind, license, approved_by from spot_facts where spot_id = %s", (a,)).fetchone()
    assert (f["value"], f["source_kind"], f["license"], f["approved_by"]) == ("1989年", "wikidata", "CC0 1.0", "import:wikidata")
    s = {r["spot_id"]: r["score"] for r in db.execute("select spot_id, score from spot_importance").fetchall()}
    assert s[a] > s[b] > 0 and s[c] == 0
    # もう一度実行しても同じ（信号は上書き、事実は重複しない）
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        st = ii.run(conn, today=date(2026, 11, 19))
        conn.commit()
    assert st["facts_added"] == 0 and st["facts_skipped_existing"] == 2
