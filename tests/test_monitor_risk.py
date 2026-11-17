"""変化リスク監視：根拠の範囲（同じ文の中だけ）と、照合しない種別"""
from locadb import monitor_risk as m


def test_context_stays_in_sentence():
    text = "天神ビッグバンで再開発が進む。旧岩田屋は西大橋の近くにある。次のページへ"
    i = text.index("西大橋")
    ctx = m.context(text, i, i + 3)
    assert "再開発" not in ctx                       # 前の文の語は拾わない
    assert ctx.startswith("旧岩田屋")


def test_context_limited_to_ctx_chars():
    text = "あ" * 200 + "博多大橋" + "い" * 200
    i = text.index("博多大橋")
    assert len(m.context(text, i, i + 4)) == 2 * m.CTX + 4


def test_infra_categories_not_matched():
    assert "bridge_overpass" in m.INFRA_CATEGORIES and "river" in m.INFRA_CATEGORIES
    assert "shrine_temple" not in m.INFRA_CATEGORIES        # 建物・施設は従来どおり照合する
