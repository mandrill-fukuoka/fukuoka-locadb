"""collect_web.py の照合・許可リスト・確度・robots のテスト（外部アクセスなし）"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "laya"))
import collect_web as cw  # noqa: E402

DOMS = ["city.fukuoka.lg.jp", "ur-net.go.jp"]
PAGE = "<html><body><nav>メニュー</nav><p>鳥飼団地は福岡市城南区にある県営住宅です。管理戸数は194戸、昭和61年から昭和63年に建設。</p><script>x=1</script></body></html>"


def test_allowed():
    assert cw.allowed("https://www.city.fukuoka.lg.jp/a.html", DOMS)
    assert cw.allowed("https://city.fukuoka.lg.jp/", DOMS)
    assert not cw.allowed("https://evil-city.fukuoka.lg.jp.example.com/", DOMS)
    assert not cw.allowed("https://blog.example.com/", DOMS)


def test_html_to_text_drops_script_and_nav():
    t = cw.html_to_text(PAGE)
    assert "鳥飼団地" in t and "x=1" not in t and "メニュー" not in t


def test_verify_quote_must_exist_and_numbers_match():
    text = cw.html_to_text(PAGE)
    ok = {"attr": "scale", "value": "194戸", "quote": "管理戸数は194戸"}
    assert cw.verify(ok, text) == ""
    assert cw.verify({**ok, "quote": "管理戸数は200戸"}, text) == "引用が本文にない"
    assert cw.verify({**ok, "value": "200戸"}, text) == "数字が引用にない"
    assert cw.verify({**ok, "attr": "rumor"}, text) == "項目不正"


def test_verify_rejects_reputation_permission_and_incident():
    text = "鳥飼団地は撮影可能で人気です。火災があった。管理戸数は194戸。"
    base = {"attr": "status", "value": "x", "quote": "鳥飼団地は撮影可能で人気です"}
    assert cw.verify({**base, "value": "撮影OK"}, text) == "評判・撮影可否の語"
    assert cw.verify({**base, "value": "人気の団地"}, text) == "評判・撮影可否の語"
    assert cw.verify({"attr": "status", "value": "火災", "quote": "火災があった"}, text) == "除外語"


def test_window_requires_name_and_place_hint():
    t = cw.html_to_text(PAGE)
    assert cw.window(t, "鳥飼団地") is not None
    assert cw.window(t, "存在しない団地") is None
    assert cw.place_hint(t, "福岡県福岡市城南区鳥飼") is True
    assert cw.place_hint(t, "福岡県福岡市東区香椎") is False


def test_confidence_counts_distinct_domains():
    c = cw.confidence([("1", "built", "昭和61年", "a.jp"), ("1", "built", "昭和 61年", "b.jp"),
                       ("1", "built", "昭和61年", "a.jp"), ("1", "owner", "福岡県", "a.jp")])
    assert c[("1", "built", cw.norm("昭和61年"))] == 2
    assert c[("1", "owner", cw.norm("福岡県"))] == 1


def test_sources_file_has_no_blog_or_review_sites():
    doms = cw.load_sources(str(Path(cw.__file__).parent / "web_sources.txt"))
    assert "city.fukuoka.lg.jp" in doms
    assert not any(x in d for d in doms for x in ("blog", "tabelog", "note.com", "ameblo"))


class FakeOpener:
    def __init__(self, pages, robots=""):
        self.pages, self.robots, self.calls = pages, robots, []

    def __call__(self, url):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            if self.robots is None:
                raise OSError("404")
            return self.robots.encode(), "text/plain"
        return self.pages[url].encode(), "text/html; charset=utf-8"


def test_fetcher_respects_robots_and_interval():
    op = FakeOpener({"https://www.city.fukuoka.lg.jp/ok.html": PAGE}, "User-agent: *\nDisallow: /private/\n")
    f = cw.Fetcher(interval=0, opener=op)
    t, why = f.text("https://www.city.fukuoka.lg.jp/private/x.html")
    assert t is None and why == "robotsで拒否"
    t, why = f.text("https://www.city.fukuoka.lg.jp/ok.html")
    assert why == "" and "鳥飼団地" in t
    assert op.calls.count("https://www.city.fukuoka.lg.jp/robots.txt") == 1      # robots は1回だけ


def test_run_end_to_end_with_fakes(tmp_path):
    u1, u2 = "https://www.city.fukuoka.lg.jp/a.html", "https://www.ur-net.go.jp/b.html"
    page2 = PAGE.replace("県営住宅", "住宅")
    op = FakeOpener({u1: PAGE, u2: page2}, None)
    facts = lambda name, addr, w: [{"attr": "built", "value": "昭和61年から昭和63年", "quote": "昭和61年から昭和63年に建設"},
                                    {"attr": "owner", "value": "福岡県", "quote": "福岡県が管理"}]       # 2件目は引用が本文にない
    out = tmp_path / "o.tsv"
    rows = cw.run([("7001", "鳥飼団地", "福岡県福岡市城南区鳥飼", "団地")], {"7001": [u1, u2, "https://blog.example.com/x"]},
                  DOMS, str(out), fetcher=cw.Fetcher(interval=0, opener=op), extract=facts, key=None, log=open(tmp_path / "l", "w"))
    assert len(rows) == 2 and all(r[11] == "確度高" for r in rows)            # 2ドメインで一致
    assert all("blog.example.com" not in r[5] for r in rows)                  # 許可リスト外は読まない
    assert not any(r[3] == "owner" for r in rows)                             # 引用なしは捨てた
    head = out.read_text(encoding="utf-8").split("\n")[0].split("\t")
    assert head[:7] == ["地点ID", "地点名", "候補No", "種類", "候補の文", "出典URL", "採否（採/不採/保留）"]


def test_wrong_place_is_held(tmp_path):
    u = "https://www.city.fukuoka.lg.jp/a.html"
    op = FakeOpener({u: PAGE}, None)
    facts = lambda *a: [{"attr": "scale", "value": "194戸", "quote": "管理戸数は194戸"}]
    rows = cw.run([("7002", "鳥飼団地", "福岡県福岡市東区香椎", "団地")], {"7002": [u]}, DOMS, str(tmp_path / "o.tsv"),
                  fetcher=cw.Fetcher(interval=0, opener=op), extract=facts, log=open(tmp_path / "l", "w"))
    assert rows[0][6] == "保留" and "同名" in rows[0][7]


def test_read_spots_skips_php_warnings_and_blank_lines(tmp_path):
    f = tmp_path / "spots.tsv"
    f.write_text("地点ID\t地点名\t住所\t種別\n\nDeprecated: Function curl_close() is deprecated since 8.5\n"
                 "8075\t筥松二丁目住宅\t福岡市東区筥松二丁目\thousing_estate\n7170\t志賀島漁港\t\tfishing_port\n", encoding="utf-8")
    assert cw.read_spots(str(f)) == [("8075", "筥松二丁目住宅", "福岡市東区筥松二丁目", "housing_estate"),
                                     ("7170", "志賀島漁港", "", "fishing_port")]


def test_name_keys_absorb_prefix_and_suffix():
    assert "東箱崎" in cw.name_keys("県営東箱崎住宅")
    assert "箱崎三丁目" in cw.name_keys("UR都市機構箱崎三丁目団地31号棟")
    assert "筥松" in cw.name_keys("UR都市機構筥松団地1")
    assert "鮮魚市場" in cw.name_keys("福岡市中央卸売市場 鮮魚市場")
    assert "志賀島" in cw.name_keys("志賀島漁港")
    assert cw.window("福岡地区 東箱崎 45 3LDK 平成28年", "県営東箱崎住宅") is not None
    assert cw.window("福岡地区 高須磨 574", "県営東箱崎住宅") is None


def test_name_keys_handle_futo_variants():
    assert "博多ふ頭" in cw.name_keys("博多埠頭")
    assert cw.window("博多ふ頭 係留施設 野積場", "博多埠頭") is not None


def test_value_must_be_words_from_the_quote():
    text = "UR賃貸住宅TOP 箱崎三丁目 各所でリフォームが進んでいる 福岡県が管理"
    # 実際の試作で出た失敗：引用が主張を支えていない
    assert cw.verify({"attr": "owner", "value": "UR都市機構が管理", "quote": "UR賃貸住宅TOP"}, text) == "値が引用の語でない"
    assert cw.verify({"attr": "status", "value": "稼働中でリフォーム中", "quote": "各所でリフォームが進んでいる"}, text) == "値が引用の語でない"
    assert cw.verify({"attr": "owner", "value": "福岡県", "quote": "福岡県が管理"}, text) == ""


def test_table_cells_are_separated_and_kanji_chome_does_not_break_place_hint():
    assert cw.html_to_text("<table><tr><td>高須磨</td><td>574</td><td>2DK</td></tr></table>") == "高須磨 574 2DK"
    assert cw.place_hint("福岡市 東区 東箱崎 45 3LDK", "福岡市東区箱崎五丁目") is True
    assert cw.place_hint("福岡市 東区 東箱崎 45 3LDK", "福岡市東区香椎一丁目") is False


def test_log_keeps_discarded_value_and_empty_extraction(tmp_path):
    u = "https://www.city.fukuoka.lg.jp/a.html"
    op = FakeOpener({u: PAGE}, None)
    log = tmp_path / "l"
    cw.run([("1", "鳥飼団地", "福岡市城南区", "団地")], {"1": [u]}, DOMS, str(tmp_path / "o.tsv"), fetcher=cw.Fetcher(interval=0, opener=op),
           extract=lambda *a: [{"attr": "owner", "value": "福岡県", "quote": "福岡市城南区にある"}], log=open(log, "w"))
    assert "値が引用の語でない" in log.read_text(encoding="utf-8") and "値=福岡県" in log.read_text(encoding="utf-8")
    cw.run([("1", "鳥飼団地", "福岡市城南区", "団地")], {"1": [u]}, DOMS, str(tmp_path / "o.tsv"), fetcher=cw.Fetcher(interval=0, opener=op),
           extract=lambda *a: [], log=open(log, "w"))
    assert "事実なし" in log.read_text(encoding="utf-8")


def test_window_picks_the_fact_dense_occurrence_not_the_first():
    text = ("お問い合わせ 福岡市中央卸売市場 鮮魚市場 TEL 092-711-6414 " + "。" * 2000 +
            " 福岡市中央卸売市場鮮魚市場の概要 開設許可 昭和30年2月22日 敷地面積 120,400m2 冷蔵庫 卸売場")
    w = cw.window(text, "福岡市中央卸売市場 鮮魚市場")
    assert "敷地面積" in w and "お問い合わせ" not in w


def test_window_for_table_rows_starts_at_the_spot_row():
    text = "位置 福岡市 東区 高須磨 574 2DK 昭和57年~59年 位置 福岡市 東区 東箱崎 45 3LDK 平成28年 位置 福岡市 早良区 内野 17"
    w = cw.window(text, "県営東箱崎住宅")
    assert w.startswith("福岡市 東区 東箱崎") or "高須磨" not in w[:10]
    assert "平成28年" in w


def test_notes_and_minimum_quote():
    assert cw.verify({"attr": "scale", "value": "574", "quote": "574"}, "高須磨 574") == "引用が短すぎる"
    assert cw.verify({"attr": "owner", "value": "UR", "quote": "UR賃貸住宅TOP"}, "UR賃貸住宅TOP") == "引用に管理者を示す語がない"
    assert "実施主体" in "".join(cw.fact_notes("小呂島漁港", {"attr": "owner", "quote": "事業実施主体:福岡県"}))
    assert "管理開始年" in "".join(cw.fact_notes("箱崎三丁目団地", {"attr": "built", "quote": "昭和51年から管理がスタート"}))
    assert "団地全体" in "".join(cw.fact_notes("箱崎三丁目団地31号棟", {"attr": "scale", "quote": "戸数 351"}))
    assert cw.fact_notes("箱崎三丁目団地31号棟", {"attr": "owner", "quote": "UR都市機構が管理"}) == []


def test_unreadable_extractor_output_is_logged(tmp_path):
    u = "https://www.city.fukuoka.lg.jp/a.html"
    log = tmp_path / "l"
    cw.run([("1", "鳥飼団地", "福岡市城南区", "団地")], {"1": [u]}, DOMS, str(tmp_path / "o.tsv"),
           fetcher=cw.Fetcher(interval=0, opener=FakeOpener({u: PAGE}, None)), extract=lambda *a: None, log=open(log, "w"))
    assert "JSONでない" in log.read_text(encoding="utf-8")


def test_neighbor_row_value_is_rejected_and_value_order_free():
    # 表で隣の団地の年を取り違えた事故（東箱崎: 平成28年が正、昭和61年は別の団地の行）の再発防止
    row = "位置 福岡市 東区 東箱崎 45 3LDK 平成28年 " + "別の団地 " * 80 + "位置 福岡市 西区 内野 17 昭和61年 3LDK"
    good = {"attr": "built", "value": "平成28年", "quote": "平成28年"}
    bad = {"attr": "built", "value": "昭和61年", "quote": "昭和61年"}
    assert cw.verify(good, row, "県営東箱崎住宅") == ""
    assert cw.verify(bad, row, "県営東箱崎住宅").startswith("引用が地点名から離れている")
    # 語順が違っても、引用が値を支えていれば通る。無関係な語は通らない
    t = "団地 箱崎三丁目 戸数\n351 管理"
    assert cw.verify({"attr": "scale", "value": "351戸", "quote": "戸数\n351"}, t, "箱崎三丁目") == ""
    assert cw.verify({"attr": "owner", "value": "福岡県", "quote": "UR賃貸住宅TOP 管理"}, "箱崎三丁目 UR賃貸住宅TOP 管理", "箱崎三丁目") == "値が引用の語でない"
    # 長い文は離れていても通る（散文のページ）
    prose = "箱崎三丁目団地 " + "あ" * 500 + " 昭和51年から管理がスタートした歴史のある建物"
    assert cw.verify({"attr": "built", "value": "昭和51年", "quote": "昭和51年から管理がスタートした歴史のある建物"}, prose, "箱崎三丁目団地") == ""


def test_no_address_is_note_not_hold(tmp_path):
    spots = [("1", "小呂島漁港", "", "fishing_port")]
    text = "小呂島漁港 管理者 福岡県"
    class F:
        def text(self, u): return text, ""
    out = tmp_path / "o.tsv"
    rows = cw.run(spots, {"1": ["https://www.pref.fukuoka.lg.jp/a.html"]}, ["pref.fukuoka.lg.jp"], str(out), fetcher=F(),
                  extract=lambda *a, **k: [{"attr": "owner", "value": "福岡県", "quote": "管理者 福岡県"}], log=open(os.devnull, "w"))
    assert rows[0][6] == "" and "住所なし" in rows[0][7]
