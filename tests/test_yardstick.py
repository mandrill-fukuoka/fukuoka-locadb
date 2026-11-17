"""評価の物差し（tools/yardstick.py）の回帰テスト（2026-11-17 順1）"""
import json
import re

from test_routes import add
from tools import yardstick as y


def route(*groups):
    return {"spots": [{"name": f"地点{i}", "group": f"名{g}", "grp_code": g} for i, g in enumerate(groups)]}


def test_covers_needs_the_sequence_in_order():
    assert y.covers(route("a", "b", "c"), ["a", "c"])          # 間に別の地点があってよい
    assert not y.covers(route("c", "a"), ["a", "c"])           # 順番が違う
    assert not y.covers(route("a", "a"), ["a", "b"])
    assert y.covers(route("river"), ["water"]) and not y.covers(route("road"), ["water"])   # 水辺は川・海などを含む
    assert y.covers(route("a"), [])                            # 景観を読み取れなかったときは流れがあれば満たす


def test_judge_scene_without_routes_is_ng():
    r = y.judge_scene({"sequence": ["a"]}, [])
    assert r["status"] == "ng" and r["routes"] == 0 and r["match"] == 0.0
    r = y.judge_scene({"sequence": ["a", "b"]}, [{**route("a", "b"), "match": 0.8}, {**route("x"), "match": 0.9}])
    assert r["status"] == "ok" and r["match"] == 0.8 and r["routes"] == 2   # 1位だけを見る


def test_compare_regressions_improvements_unmeasured():
    base = {"a": "ok", "b": "ng", "c": "ok", "d": None}
    cur = {"a": "ng", "b": "ok", "c": "ok", "d": "ok"}
    assert y.compare(cur, base) == {"regressions": ["a"], "improvements": ["b"], "unmeasured": ["d"]}
    assert y.compare({}, {"a": "ok"})["regressions"] == ["a"]  # 結果に無くなったものも後退


def test_match_mean_ignores_scenes_without_routes():
    assert y.match_mean([{"routes": 2, "match": 0.5}, {"routes": 1, "match": 0.7}, {"routes": 0, "match": 0.0}]) == 0.6
    assert y.match_mean([]) == 0.0


def test_place_cuts_file_is_well_formed():
    doc = json.loads(y.CUTS.read_text(encoding="utf-8"))
    nos = [c["no"] for c in doc["cuts"]]
    assert len(nos) == len(set(nos)) and doc["total_cuts"] == 66
    for c in doc["cuts"]:
        re.compile(c["pattern"])
        assert c["baseline"] in (None, "ok", "ng")
        assert c["categories"] is None or isinstance(c["categories"], list)


def test_scene_interpretation_matches_the_baseline():
    """シーン文の解釈（web/app/scene.php）が変わったら、基準を意図して書き直す（python tools/yardstick.py interp --write）"""
    cur = y.interpret_all(y.load_scenes())
    base = json.loads(y.INTERP_BASE.read_text(encoding="utf-8"))
    changed = [s for s in cur if cur[s] != base.get(s)]
    assert not changed and set(cur) == set(base), changed


def test_check_cut_uses_name_alt_names_category_and_skips_archived(db):
    add(db, "親不孝通り", "alley", 0, 0, alt_names=["親富孝通り"])
    add(db, "元岡の田園", "coast", 500, 0)
    add(db, "元岡の田園", "alley", 800, 0)
    add(db, "旧・中洲の路地", "alley", 1000, 0, data_status="archived")
    assert y.check_cut(db, {"no": 60, "name": "x", "pattern": "親富孝", "categories": None})["status"] == "ok"   # 別名で見つかる（種類は便宜上、seed 済みのものを使う）
    r = y.check_cut(db, {"no": 11, "name": "x", "pattern": "元岡.*田園", "categories": ["coast"]})
    assert r["status"] == "ok" and len(r["found"]) == 1                                                        # 種類で絞る
    assert y.check_cut(db, {"no": 61, "name": "x", "pattern": "中洲.*路地", "categories": None})["status"] == "ng"  # アーカイブ済みは除く
    assert y.check_cut(db, {"no": 99, "name": "x", "pattern": "存在しない", "categories": None})["status"] == "ng"
