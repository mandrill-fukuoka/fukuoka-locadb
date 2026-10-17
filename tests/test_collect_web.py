"""collect_web.py の照合・許可リスト・確度・robots のテスト（外部アクセスなし）"""
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
    ok = {"attr": "scale", "value": "管理戸数194戸", "quote": "管理戸数は194戸"}
    assert cw.verify(ok, text) == ""
    assert cw.verify({**ok, "quote": "管理戸数は200戸"}, text) == "引用が本文にない"
    assert cw.verify({**ok, "value": "管理戸数200戸"}, text) == "数字が引用にない"
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
    facts = lambda name, addr, w: [{"attr": "built", "value": "昭和61年から63年に建設", "quote": "昭和61年から昭和63年に建設"},
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
