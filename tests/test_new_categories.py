"""回帰テスト：修正提案2Aで足した種別（踏切・トンネル・立体駐車場・灯台・給水塔・廃線跡・銭湯）。DB なし"""
import pytest

from locadb.categories import CATEGORIES, PRIORITY, categories_of


@pytest.mark.parametrize("tags,osm_type,closed,want", [
    ({"railway": "level_crossing"}, "n", False, ["level_crossing"]),
    ({"railway": "crossing"}, "n", False, []),                                       # 歩行者の横断は踏切ではない
    ({"highway": "service", "tunnel": "yes", "name": "中央地下道"}, "w", False, ["underpass"]),
    ({"highway": "residential", "tunnel": "yes", "name": "市道12号線"}, "w", False, []),   # 番号だけの道は除く
    ({"highway": "residential", "tunnel": "yes"}, "w", False, []),                    # 名前のないトンネルは取らない
    ({"amenity": "parking", "parking": "multi-storey"}, "w", True, ["parking_multi"]),
    ({"amenity": "parking", "parking": "surface"}, "w", True, ["seaside_parking"]),     # 海の近くかは osm_source で絞る（2026-10-29）
    ({"man_made": "lighthouse"}, "n", False, ["lighthouse"]),                         # 2026-10-29：渡船場と分けた
    ({"amenity": "ferry_terminal", "name": "渡船場"}, "n", False, ["harbour_structure"]),
    ({"amenity": "ferry_terminal"}, "n", False, []),
    ({"man_made": "water_tower"}, "n", False, ["industrial_structure"]),
    ({"man_made": "storage_tank"}, "w", True, []),                                    # 名前のないタンクは取らない
    ({"man_made": "storage_tank", "name": "ガスタンク"}, "w", True, ["industrial_structure"]),
    ({"railway": "abandoned", "name": "旧国鉄線"}, "w", False, ["disused_railway"]),
    ({"railway": "abandoned"}, "w", False, []),
    ({"amenity": "public_bath", "name": "松の湯"}, "w", True, ["sento"]),
])
def test_new_category_rules(tags, osm_type, closed, want):
    assert categories_of(tags, osm_type, closed) == want


def test_new_categories_use_existing_groups():
    from locadb.categories import LANDSCAPE_GROUPS
    for c in ("level_crossing", "underpass", "parking_multi", "harbour_structure", "industrial_structure", "disused_railway", "sento"):
        assert CATEGORIES[c][1] in LANDSCAPE_GROUPS and CATEGORIES[c][2] == "point"
        assert c in PRIORITY


def test_semicolon_name_display():
    from locadb.categories import display_name
    assert display_name({"name": "名島踏切;貝塚2号踏切"}, "level_crossing") == "名島踏切"
    assert display_name({"name": "和白踏切"}, "level_crossing") == "和白踏切"
    assert display_name({"name": "箱崎橋;県道112号", "highway": "primary", "bridge": "yes"}, "bridge_overpass") == "箱崎橋"   # 橋は従来どおり
