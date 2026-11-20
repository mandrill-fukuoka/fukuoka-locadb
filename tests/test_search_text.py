"""全文検索（順5・S3）：空白で区切った語が、名前・別名・住所・種類・タグ・説明文・事実のどれかに入っていればよい（語のすべてが必要）"""
from test_routes import add


def search(db, **kw):
    args = ", ".join(f"{k} => %s" for k in kw)
    return db.execute(f"select search_spots({args}) as r", list(kw.values())).fetchone()["r"]


def names(rows):
    return sorted(r["name"] for r in rows)


def make(db):
    a = add(db, "海辺の塔", "coast", 0, 0, address="福岡市早良区百道浜2-1", alt_names=["シーサイドタワー"])
    b = add(db, "古い石段", "coast", 300, 0, address="福岡市博多区")
    c = add(db, "港の広場", "coast", 600, 0, address="福岡市中央区")
    db.execute("update spots set summary = '朝日が海に映える展望の場所。', summary_by = 'tester' where id = %s", (c,))
    db.execute("insert into spot_facts (spot_id, item, value, note, source_kind, source_url, approved_by) "
               "values (%s, '規模', '高さ234m', '鉄骨造', 'osm', 'https://example.com/x', 't')", (a,))
    db.execute("insert into spot_tags (spot_id, tag_code, origin, score) values (%s, 'nostalgic', 'human', 1)", (b,))
    db.commit()
    return a, b, c


def test_terms_match_any_field_all_terms_required(db):
    make(db)
    assert names(search(db, p_q="早良区")) == ["海辺の塔"]                       # 住所
    assert names(search(db, p_q="シーサイド")) == ["海辺の塔"]                    # 別名
    assert names(search(db, p_q="展望")) == ["港の広場"]                          # 説明文
    assert names(search(db, p_q="234m")) == ["海辺の塔"]                          # 事実カードの値
    assert names(search(db, p_q="鉄骨")) == ["海辺の塔"]                          # 事実カードの細目
    assert names(search(db, p_q="懐かしい")) == ["古い石段"]                      # タグの名前
    assert names(search(db, p_q="早良区 塔")) == ["海辺の塔"]                     # 語はすべて必要（空白区切り）
    assert names(search(db, p_q="早良区　展望")) == []                           # 全角空白でも区切る。両方に当てはまる地点はない
    assert names(search(db, p_q="  ")) == names(search(db))                      # 空白だけは指定なしと同じ


def test_unapproved_or_revoked_facts_are_not_searched(db):
    a, b, c = make(db)
    db.execute("update spot_facts set status = 'revoked', revoked_by = 't', revoked_at = now() where spot_id = %s", (a,))
    db.commit()
    assert search(db, p_q="234m") == []


def test_similar_spots(db):
    a = add(db, "基点", "coast", 0, 0)
    b = add(db, "近い同種", "coast", 300, 0)
    c = add(db, "雰囲気が同じ", "coast", 900, 0)
    d = add(db, "別の種類", "shrine_temple", 100, 0)
    e = add(db, "遠すぎる同種", "coast", 20000, 0)
    for sid in (a, c):
        db.execute("insert into spot_tags (spot_id, tag_code, origin, score) values (%s, 'lonely', 'human', 1)", (sid,))
    db.commit()
    r = db.execute("select spots_similar(%s) as r", (a,)).fetchone()["r"]
    assert [x["name"] for x in r] == ["雰囲気が同じ", "近い同種"]            # 共通タグが多い順→近い順。別の種類・遠すぎるものは出さない
    assert db.execute("select spots_similar(%s, 1) as r", (a,)).fetchone()["r"][0]["name"] == "雰囲気が同じ"


def test_spots_without_address_or_alt_names_do_not_match_everything(db):
    add(db, "住所なし", "coast", 0, 0)                     # address・alt_names が null の地点
    add(db, "住所あり", "coast", 300, 0, address="福岡市中央区")
    assert search(db, p_q="存在しない語") == []
    assert names(search(db, p_q="中央区")) == ["住所あり"]
