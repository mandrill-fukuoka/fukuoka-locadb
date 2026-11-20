"""写真候補のおすすめ度：ローマ字・英語名・カテゴリ名で一致したものは「見込み低」にしない（順4。DB変更270）"""


def sg(db, *a):
    return db.execute("select photo_suggest(%s,%s,%s,%s,%s,%s,%s) r", a).fetchone()["r"]


def test_match_kind_lifts_low_to_unknown_but_not_high(db):
    far = ("commons", False, "", "File:a.jpg", "park", 80)
    assert sg(db, *far, None) == "low"                    # 名前不一致で30m以上
    assert sg(db, *far, "romaji") == "unknown" and sg(db, *far, "category") == "unknown"
    assert sg(db, "commons", False, "", "File:a.jpg", "school", 5, "romaji") == "unknown"       # 史跡・学校・官公庁の規則より先
    assert sg(db, "commons", False, "駅名標", "File:a.jpg", "park", 80, "romaji") == "low"       # 看板類の語が優先
    assert sg(db, "commons", True, "", "File:a.jpg", "park", 5, "ja") == "high"
