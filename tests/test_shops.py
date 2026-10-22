"""回帰テスト：店舗・飲食（8種別・景観グループ shop）。ルールは DB なし、検索への混ざり方は DB あり"""
import pytest

from locadb.categories import CATEGORIES, LANDSCAPE_GROUPS, PRIORITY, categories_of
from osmworld import World, spots


@pytest.mark.parametrize("tags,want", [
    ({"amenity": "restaurant", "name": "一蘭"}, ["restaurant"]),
    ({"amenity": "fast_food", "name": "マクドナルド", "brand": "McDonald's"}, ["restaurant"]),   # チェーンも取る
    ({"amenity": "cafe", "name": "珈琲店"}, ["cafe"]),
    ({"amenity": "ice_cream", "name": "アイス屋"}, ["cafe"]),
    ({"amenity": "bar", "name": "バーA"}, ["bar"]),
    ({"amenity": "pub", "name": "パブB"}, ["bar"]),
    ({"shop": "clothes", "name": "古着屋"}, ["fashion_shop"]),
    ({"shop": "books", "name": "書店C"}, ["bookstore"]),
    ({"shop": "variety_store", "name": "雑貨D"}, ["zakka_shop"]),
    ({"shop": "second_hand", "name": "リサイクル店"}, ["zakka_shop"]),
    ({"shop": "supermarket", "name": "スーパーE"}, ["supermarket"]),
    ({"shop": "bakery", "name": "パン屋"}, ["other_shop"]),
    ({"amenity": "restaurant"}, []),                         # 名前のない店は取らない
    ({"shop": "convenience", "name": "コンビニ"}, []),        # コンビニは対象外（ロジ情報で使う）
    ({"shop": "mall", "name": "ショッピングモール"}, ["commercial_facility"]),   # モール・百貨店は従来どおり商業施設
])
def test_shop_rules(tags, want):
    assert categories_of(tags, "n", False) == want


def test_shop_group_and_categories():
    cats = ["restaurant", "cafe", "bar", "fashion_shop", "bookstore", "zakka_shop", "supermarket", "other_shop"]
    assert "shop" in LANDSCAPE_GROUPS
    for c in cats:
        assert CATEGORIES[c][1] == "shop" and CATEGORIES[c][2] == "point" and c in PRIORITY


def test_shops_excluded_from_default_search_but_found_by_group_category_or_word(db, tmp_path):
    w = World()
    w.point(10, 130.39, 33.585, amenity="place_of_worship", name="警固神社")
    w.point(11, 130.3905, 33.5852, amenity="cafe", name="珈琲店ひだまり")
    w.run(db, tmp_path)
    def names(**kw):
        args = ", ".join(f"{k} => %({k})s" for k in kw)
        return {x["name"] for x in db.execute(f"select search_spots({args}) r", kw).fetchone()["r"]}
    assert names(p_q=None) == {"警固神社"}
    assert "珈琲店ひだまり" in names(p_group="shop") and "珈琲店ひだまり" in names(p_category="cafe") and "珈琲店ひだまり" in names(p_q="ひだまり")
    by = {s["name"]: s for s in spots(db)}
    near = db.execute("select spots_nearby(%s, 500) r", (by["警固神社"]["id"],)).fetchone()["r"]
    assert all(x["name"] != "珈琲店ひだまり" for x in near)                          # 景観の近くの地点に店は混ぜない
    near2 = db.execute("select spots_nearby(%s, 500) r", (by["珈琲店ひだまり"]["id"],)).fetchone()["r"]
    assert any(x["name"] == "警固神社" for x in near2)
