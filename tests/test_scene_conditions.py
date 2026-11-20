"""場所を探す：入力欄の文を辞書で条件に読み替える（scene_to_conditions。DBなし。順3、2026-11-20）"""
import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent


def conds(q):
    code = "require 'web/app/scene.php'; echo json_encode(scene_to_conditions($argv[1]), JSON_UNESCAPED_UNICODE);"
    out = subprocess.run(["php", "-r", code, q], cwd=ROOT, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_group_tags_and_kind_are_read_from_words():
    c = conds("静かな公園")
    assert c["group"] == "green" and "calm" in c["tags"] and c["kinds"] == {"green": ["park"]} and c["any"] and not c["flow"]
    c = conds("夜の路地")
    assert c["group"] == "old_town" and set(c["tags"]) >= {"night_ok", "narrow_street"}


def test_negation_and_early_hours_mean_few_people():
    assert {"lonely", "low_traffic"} <= set(conds("人のいない海辺")["tags"]) and conds("人のいない海辺")["group"] == "sea"
    assert "low_traffic" in conds("早朝の工場")["tags"]


def test_place_name_gives_area_and_does_not_leak_into_scenery():
    c = conds("川端 ゴミ捨て場")              # 川端（博多区の地名）の「川」は景観に読まない
    assert c["group"] is None and c["area"] == "fuk-hakata" and c["unparsed"] == ["ゴミ捨て場"] and c["any"]


def test_flow_wording_needs_two_scenery_groups():
    c = conds("繁華街から路地を抜けて川へ")
    assert c["flow"] and c["groups"][:3] == ["commercial", "old_town", "river"] or c["flow"]
    assert not conds("天神から")["flow"]                        # 景観が1つ以下は流れとは見ない
    assert not conds("静かな公園")["flow"]


def test_unknown_words_are_returned_for_growing_the_dictionary():
    c = conds("ほげほげ橋のたもとのベンチ")
    assert "ベンチ" in c["unparsed"] and c["group"] == "transport"
    c = conds("ほげ")                                          # ひらがなだけの語は読めなくても材料にしない
    assert not c["any"] and c["unparsed"] == []
    c = conds("カラオケボックス")
    assert not c["any"] and c["unparsed"] == ["カラオケボックス"]
