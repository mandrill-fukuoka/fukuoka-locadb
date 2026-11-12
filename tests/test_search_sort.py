"""場所を探す（search_spots）の全件数と並び順の回帰テスト（2026-11-12）"""
import json

from test_routes import add


def search(db, **kw):
    args = ", ".join(f"{k} => %s" for k in kw)
    return db.execute(f"select search_spots({args}) as r", list(kw.values())).fetchone()["r"]


def make(db):
    a = add(db, "あ岸", "coast", 0, 0, address="福岡市中央区ま", relief_m=5)
    i = add(db, "い岸", "coast", 300, 0, address="福岡市中央区あ", relief_m=30)
    u = add(db, "う岸", "coast", 600, 0, address="福岡市中央区ず")
    n = add(db, "（無名）岸", "coast", 900, 0, address="福岡市中央区ん", relief_m=99)
    db.execute("update spots set created_at = %s, updated_at = %s where id = %s", ("2026-01-01", "2026-03-01", a))
    db.execute("update spots set created_at = %s, updated_at = %s where id = %s", ("2026-02-01", "2026-01-01", i))
    db.execute("update spots set created_at = %s, updated_at = %s where id = %s", ("2026-03-01", "2026-02-01", u))
    db.commit()


def names(rows):
    return [r["name"] for r in rows]


def test_total_and_rank_are_returned_for_every_row(db):
    make(db)
    rows = search(db, p_category="coast")
    assert len(rows) == 4 and {r["total"] for r in rows} == {4} and [r["rank"] for r in rows] == [1, 2, 3, 4]
    page = search(db, p_category="coast", p_offset=3, p_limit=2)          # 最後のページ：全件数は変わらない
    assert len(page) == 1 and page[0]["total"] == 4 and page[0]["rank"] == 4
    assert search(db, p_category="coast", p_offset=10) == []


def test_sort_orders_and_unnamed_always_last(db):
    make(db)
    by = lambda s: names(search(db, p_category="coast", p_sort=s))
    assert by("name") == ["あ岸", "い岸", "う岸", "（無名）岸"]
    assert by("address") == ["い岸", "う岸", "あ岸", "（無名）岸"]                                         # あ < ず < ま
    assert by("created") == ["う岸", "い岸", "あ岸", "（無名）岸"]
    assert by("updated") == ["あ岸", "う岸", "い岸", "（無名）岸"]
    assert by("relief") == ["い岸", "あ岸", "う岸", "（無名）岸"]          # 高い順。標高差のない地点は後ろ
    # 並び順を変えても件数と、ページ送りの順は同じ
    page1 = names(search(db, p_category="coast", p_sort="created", p_limit=2))
    page2 = names(search(db, p_category="coast", p_sort="created", p_offset=2, p_limit=2))
    assert page1 + page2 == by("created")


def test_unknown_or_empty_sort_is_the_default_order(db):
    make(db)
    base = names(search(db, p_category="coast"))
    for s in (None, "", "relevance", "nonsense; drop table spots"):
        assert names(search(db, p_category="coast", p_sort=s)) == base
