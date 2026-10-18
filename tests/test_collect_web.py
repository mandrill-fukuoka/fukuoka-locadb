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
    head = out.read_text(encoding="utf-8-sig").split("\n")[0].split("\t")
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


TABLE = """<table><tr><th>市町村</th><th>団地名</th><th>管理戸数</th><th>間取り</th><th>建設年度</th></tr>
<tr><td>福岡市<br>東区</td><td>東箱崎</td><td>４５</td><td>3LDK</td><td>平成２８年</td></tr>
<tr><td>福岡市<br>西区</td><td>内野</td><td>１７</td><td>3LDK</td><td>昭和６１年</td></tr>
<tr><td>福岡市<br>東区</td><td>同名</td><td>10</td><td>2DK</td><td>昭和40年</td></tr>
<tr><td>北九州市<br>小倉北区</td><td>同名</td><td>20</td><td>2DK</td><td>昭和50年</td></tr></table>"""


def test_table_facts_reads_only_the_matching_row():
    f, is_table = cw.table_facts(TABLE, "県営東箱崎住宅")
    assert is_table
    got = {x["attr"]: x["value"] for x in f}
    assert got == {"scale": "45戸", "built": "平成28年"}          # 隣の内野の昭和61年は出ない
    text = cw.html_to_text(TABLE)
    assert all(cw.verify(x, text, "県営東箱崎住宅") == "" for x in f)
    # 同名の行が複数 → 住所の区でしぼる。しぼれなければ出さない
    assert cw.table_facts(TABLE, "同名", "福岡市東区X")[0][0]["value"] == "10戸"
    assert cw.table_facts(TABLE, "同名", "")[0] == []
    # 表はあるが地点がない／表がないページ
    assert cw.table_facts(TABLE, "存在しない団地") == ([], True)
    assert cw.table_facts("<p>表なし</p>", "東箱崎") == ([], False)


def test_label_value_facts_and_run_uses_table_instead_of_llm(tmp_path):
    lv = cw.label_value_facts("鮮魚市場 業務開始 昭和30年6月21日 敷地面積 120,400m2 ほか")
    assert [(x["attr"], x["value"]) for x in lv] == [("built", "昭和30年6月21日"), ("scale", "120,400m2")]

    class F:
        last_html = TABLE
        def text(self, u): return cw.html_to_text(TABLE), ""
    called = []
    def llm(*a, **k):
        called.append(1); return [{"attr": "built", "value": "昭和61年", "quote": "昭和６１年"}]   # 取り違えるAI
    out = tmp_path / "o.tsv"
    rows = cw.run([("1", "県営東箱崎住宅", "福岡市東区箱崎五丁目", "housing_estate")], {"1": ["https://www.pref.fukuoka.lg.jp/a.html"]},
                  ["pref.fukuoka.lg.jp"], str(out), fetcher=F(), extract=llm, log=open(os.devnull, "w"))
    assert not called                                    # 表のページではAIを呼ばない
    assert sorted(r[4] for r in rows) == ["建築年・竣工年・開設年：平成28年", "規模（面積・階数・戸数・延長）：45戸"]
    assert all("表の列" in r[7] for r in rows)


def test_malformed_llm_items_are_dropped_not_crash(tmp_path):
    class F:
        last_html = None
        def text(self, u): return "新天町商店街 創設 昭和21年10月", ""
    for bad in (["設立 昭和21年"], {"facts": 1}, [None, 3]):
        log = tmp_path / "l.txt"
        with open(log, "w") as lg:
            rows = cw.run([("1", "新天町商店街", "福岡市中央区天神二丁目", "shotengai")], {"1": ["https://shintencho.or.jp/o/"]},
                          ["shintencho.or.jp"], str(tmp_path / "o.tsv"), fetcher=F(), extract=lambda *a, **k: bad, log=lg)
        assert [r[4] for r in rows] == ["建築年・竣工年・開設年：昭和21年10月"]      # 項目名と値を機械で拾った分は残る


def test_shotengai_suffix_company_founding_and_dup_numbers():
    assert "新天町" in cw.name_keys("新天町商店街") and "川端" in cw.name_keys("川端商店街")
    assert cw.label_value_facts("博多港ふ頭株式会社 設立年月日 平成5年4月30日") == []          # 会社の設立日は拾わない
    assert [(x["attr"], x["value"]) for x in cw.label_value_facts("新天町 創設 1946年10月")] == [("built", "1946年10月")]
    assert cw.label_value_facts("管理戸数 351")[0]["value"] == "351戸"
    assert "不自然" in "；".join(cw.fact_notes("新天町商店街", {"attr": "scale", "value": "8,361,64m2", "quote": "敷地面積 8,361,64m2"}))
    assert "不自然" not in "；".join(cw.fact_notes("x", {"attr": "scale", "value": "120,400m2", "quote": "q"}))

    class F:
        last_html = None
        def text(self, u): return "UR箱崎三丁目団地 戸数 351 ほか", ""
    import tempfile
    d = tempfile.mkdtemp()
    rows = cw.run([("1", "UR都市機構箱崎三丁目団地31号棟", "福岡市東区箱崎三丁目", "housing_estate")], {"1": ["https://ur-net.go.jp/a"]}, ["ur-net.go.jp"],
                  d + "/o.tsv", fetcher=F(), extract=lambda *a, **k: [{"attr": "scale", "value": "351戸", "quote": "戸数 351"}], log=open(os.devnull, "w"))
    assert len(rows) == 1                                   # AIと機械の同じ値は1件にまとめる


def test_output_has_bom_for_excel(tmp_path):
    class F:
        last_html = None
        def text(self, u): return "新天町商店街 創設 1946年10月", ""
    out = tmp_path / "o.tsv"
    cw.run([("1", "新天町商店街", "", "shotengai")], {"1": ["https://shintencho.or.jp/o/"]}, ["shintencho.or.jp"], str(out), fetcher=F(),
           extract=lambda *a, **k: [], log=open(os.devnull, "w"))
    assert out.read_bytes()[:3] == b"\xef\xbb\xbf"


def test_build_kw_web_sentences(tmp_path):
    import build_kw_web as b
    tsv = tmp_path / "r.tsv"
    H = "\t".join(b.COLS)
    rows = [
        ["8425", "UR箱崎三丁目団地31号棟", "1", "scale", "規模：351戸", "u", "採", "地点は棟、記載は団地全体の可能性", "-", "web:x", "戸数 351", "要確認", "d"],
        ["8757", "県営東箱崎住宅", "1", "built", "建築年：平成28年", "u", "採", "表の列『建設年度』を機械的に読んだ", "-", "web:x", "東箱崎 45 平成28年", "要確認", "d"],
        ["7262", "小呂島漁港", "1", "owner", "管理者：福岡県", "u", "不採", "", "-", "web:x", "事業実施主体:福岡県", "要確認", "d"],
        ["9483", "鮮魚市場", "1", "built", "建築年：昭和30年6月21日", "u", "", "", "-", "web:x", "業務開始 昭和30年6月21日", "要確認", "d"],
    ]
    tsv.write_text(H + "\n" + "\n".join("\t".join(r) for r in rows) + "\n", encoding="utf-8-sig")
    out = tmp_path / "k.php"
    b.main(str(tsv), str(out))
    t = out.read_text(encoding="utf-8")
    assert "団地全体の戸数は351戸。" in t and "建設年度は平成28年。" in t
    assert "7262" not in t and "9483" not in t                   # 不採・未記入は入らない
