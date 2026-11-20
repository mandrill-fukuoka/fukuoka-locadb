"""写真候補のふるい直し：名前の照合（ローマ字・英語名・カテゴリ名）と、同じ写真の順位（DBなし。順4）"""
from locadb.photo_match import fold, match_kind, name_keys, romaji
from tools.photo_rescreen import dup_order, fetch_categories, old_rule


def test_fold_unifies_romanization_variants():
    assert fold("Hiyoshi-jinja") == fold("Hiyoshijinja") == fold("HIYOSI JINJA")
    assert fold("Ōhori Kōen") == fold("Ohori Koen") == fold("Oohori Kouen")          # 長音
    assert fold("Kushida") == fold("Kushita")                                          # 連濁


def test_romaji_of_japanese_names():
    assert romaji("日吉神社") == fold("Hiyoshi Jinja")
    assert romaji("Fukuoka Tower") == fold("fukuoka tower")                            # 英語はならすだけ


def test_match_kind_japanese_romaji_category_and_none():
    assert match_kind(["警固神社"], "File:警固神社 本殿.jpg", "") == "ja"
    assert match_kind(["日吉神社"], "File:Hiyoshijinja Shrine in Fukuoka.jpg", "") == "romaji"
    assert match_kind(["大濠公園"], "File:Ohori Park 2019.jpg", "") == "romaji"
    assert match_kind(["櫛田神社"], "File:IMG_1234.jpg", "", ["Kushida Shrine"]) == "category"
    assert match_kind(["櫛田神社"], "File:IMG_1234.jpg", "", []) is None
    assert match_kind(["警固神社"], "File:Fukuoka Station.jpg", "") is None


def test_short_or_common_names_are_not_matched_by_romaji():
    assert name_keys(["天神"]) == []                          # ありふれた地名
    assert name_keys(["西公園"]) == ["nisikoen"] or name_keys(["西公園"]) == []   # 一般名詞を除いた「西」は鍵にしない
    assert match_kind(["（無名）路地"], "File:Roji.jpg", "") is None
    assert match_kind(["山"], "File:Yama no michi.jpg", "") is None                    # 短い名前（ローマ字の鍵が5文字未満）は照合しない


def test_dup_order_puts_adopted_then_match_then_nearest_first():
    rows = [dict(id=1, page_url="u", status="candidate", new_match=False, dist_m=5),
            dict(id=2, page_url="u", status="candidate", new_match=True, dist_m=80),
            dict(id=3, page_url="u", status="candidate", new_match=True, dist_m=20),
            dict(id=4, page_url="v", status="candidate", new_match=True, dist_m=1)]
    assert dup_order(rows) == {3: 1, 2: 2, 1: 3}              # 1件だけのページは出さない
    rows[0]["status"] = "adopted"
    assert dup_order(rows)[1] == 1                            # 採用済みの地点が先頭


def test_old_rule_matches_photo_suggest_order():
    base = dict(source="commons", description="", file_title="File:a.jpg", dist_m=50, category="park")
    assert old_rule(base, True) == "high" and old_rule(base, False) == "low:名前不一致で30m以上"
    assert old_rule({**base, "description": "駅名標"}, True) == "low:看板・標識・地図"
    assert old_rule({**base, "dist_m": 5, "category": "school"}, False) == "low:史跡・学校・官公庁"
    assert old_rule({**base, "dist_m": 5}, False) == "unknown"


def test_fetch_categories_batches_and_retries():
    calls = []

    class R:
        def __init__(self, code, js): self.status_code, self._j = code, js
        def json(self): return self._j

    class S:
        def get(self, url, params=None, **kw):
            calls.append(params["titles"])
            if len(calls) == 1:
                return R(500, {})
            return R(200, {"query": {"pages": [{"title": t, "categories": [{"title": "Category:Kushida Shrine"}]} for t in params["titles"].split("|")]}})

    out = fetch_categories([f"File:{i}.jpg" for i in range(60)], S(), sleep=lambda s: None)
    assert len(calls) == 3 and len(out) == 60 and out["File:0.jpg"] == ["Kushida Shrine"]        # 500 → 再試行。50件ずつ
