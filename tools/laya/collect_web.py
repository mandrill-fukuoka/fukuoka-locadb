#!/usr/bin/env python3
"""公式ページから「事実の候補」を集める（Web版・試作）。DBには触れない。1秒間隔・直列。

流れ：地点 → 公式ページのURL（①直接指定 ②Tavily検索でURLだけ） → 取得（robots確認）
      → 手元のLLM（Ollama）で事実と原文の引用を抜く → 引用が本文にあるか機械照合
      → 複数ドメインで一致した事実に「確度高」 → 承認用TSV（collect_wiki.py と同じ列＋引用・確度）

使い方：
  python3 collect_web.py 地点.tsv 出力.tsv [--urls 直接指定.tsv] [--sources web_sources.txt]
    地点.tsv     ：地点ID・地点名・住所・種別（タブ区切り、1行目はヘッダー）
    直接指定.tsv ：地点ID・URL（検索せずに読むページ。県営住宅一覧など）
  環境変数：TAVILY_API_KEY（任意。URL探しだけに使う。なければ直接指定だけ）
            OLLAMA_URL（既定 http://localhost:11434）、OLLAMA_MODEL（既定 qwen2.5:14b）
  キーはコード・ログ・出力に書かない。

守ること：
- 検索結果の本文は使わない・保存しない（URLだけ）。本文は許可リストのサイトから自分で取得する。
- 撮影の可否は書かない。評判・口コミの言い換えは抜かない。事実（管理者・建築年・規模・現況）だけ。
- 事実は短く言い換える。原文は「引用」列に残し、承認のときに人が見比べる。
"""
import argparse, collections, csv, datetime, html.parser, json, os, re, shutil, subprocess, sys
import time, unicodedata, urllib.parse, urllib.request, urllib.robotparser

UA = "FukuokaLocaDB/1.0 (https://github.com/mandrillfuk-dev/fukuoka-locadb)"
MAX_BYTES = 2_000_000
WINDOW = 1500          # LLM に渡す本文の長さ（地点名の周辺）。表では隣の行が混ざらないよう短くする
PRE = 40               # 地点名の手前から取る長さ（表では前の行が混ざらないよう短くする）
MIN_QUOTE = 4          # 引用の最低字数（「574」のような短い引用はどこにでもあるので認めない）
FACTWORDS = re.compile(r"敷地面積|延べ?面積|延床面積|面積|戸数|階|構造|開設|開場|開業|竣工|建設|建築|築|設立|創立|管理|所有|運営|設置|事業主体|実施主体|施設")
OWNER_STRONG = re.compile(r"管理|所有|運営|設置|開設者|経営")
OWNER_WEAK = re.compile(r"実施主体|事業主体|主体")
ATTRS = {              # 集める属性（ロケハンで知りたいもの）
    "owner": "管理者・所有者",
    "built": "建築年・竣工年・開設年",
    "scale": "規模（面積・階数・戸数・延長）",
    "status": "現況（稼働・閉鎖・改修・解体予定）",
}
EXCL = re.compile(r"事故|死亡|死者|殺|自殺|逮捕|火災|爆発|墜落|災害|遭難|被害|訴訟|不祥事")
REPUTE = re.compile(r"人気|おすすめ|オススメ|絶品|話題|映え|穴場|口コミ|評判|行列")
PERMIT = re.compile(r"撮影(可|OK|できま|禁止|許可)|ロケ(可|OK)")


# ---------- 許可リスト ----------
def load_sources(path):
    doms = []
    for l in open(path, encoding="utf-8"):
        l = l.split("#", 1)[0].strip()
        if l:
            doms.append(l.split("\t")[0].strip().lower())
    return doms


def allowed(url, doms):
    h = (urllib.parse.urlparse(url).hostname or "").lower()
    return any(h == d or h.endswith("." + d) for d in doms)


# ---------- 取得（1秒間隔・直列・robots確認） ----------
class Fetcher:
    def __init__(self, interval=1.0, opener=None):
        self.interval, self.last, self.robots = interval, 0.0, {}
        self.opener = opener or self._open
        self.last_html = None          # 直近に取得したHTML（PDFのときは None）。表の読み取りに使う

    def _open(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read(MAX_BYTES), r.headers.get("Content-Type", "")

    def _wait(self):
        w = self.interval - (time.time() - self.last)
        if w > 0:
            time.sleep(w)
        self.last = time.time()

    def robots_ok(self, url):
        u = urllib.parse.urlparse(url)
        base = f"{u.scheme}://{u.netloc}"
        if base not in self.robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                self._wait()
                body, _ = self.opener(base + "/robots.txt")
                rp.parse(body.decode("utf-8", "replace").splitlines())
            except Exception:
                rp = None      # robots が取れない（404など）は制限なしとみなす
            self.robots[base] = rp
        rp = self.robots[base]
        return True if rp is None else rp.can_fetch(UA, url)

    def text(self, url):
        """(本文テキスト, 失敗理由)。HTMLと、pdftotext があればPDF。"""
        if not self.robots_ok(url):
            return None, "robotsで拒否"
        self._wait()
        self.last_html = None
        try:
            body, ctype = self.opener(url)
        except Exception as e:
            return None, f"取得失敗 {e}"
        if url.lower().endswith(".pdf") or "pdf" in ctype.lower():
            if not shutil.which("pdftotext"):
                return None, "PDF（pdftotext なし）"
            p = subprocess.run(["pdftotext", "-layout", "-", "-"], input=body, capture_output=True)
            return squeeze(p.stdout.decode("utf-8", "replace")), ""
        self.last_html = body.decode(guess_charset(body, ctype), "replace")
        return html_to_text(self.last_html), ""


def squeeze(t):
    """PDFの -layout 出力は余白が多く、窓の字数を無駄にする。空白と空行をつめる。"""
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t\u3000]+", " ", t)).strip()


def guess_charset(body, ctype):
    m = re.search(r"charset=([\w-]+)", ctype, re.I) or re.search(rb"charset=[\"']?([\w-]+)", body[:2000], re.I)
    if not m:
        return "utf-8"
    c = m.group(1)
    return (c.decode() if isinstance(c, bytes) else c).lower()


class _Text(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "footer", "header"}

    def __init__(self):
        super().__init__()
        self.out, self.depth = [], 0

    def handle_starttag(self, t, a):
        if t in self.SKIP:
            self.depth += 1
        elif t in ("br", "p", "tr", "li", "h1", "h2", "h3", "div"):
            self.out.append("\n")
        elif t in ("td", "th"):
            self.out.append(" ")

    def handle_endtag(self, t):
        if t in self.SKIP and self.depth:
            self.depth -= 1

    def handle_data(self, d):
        if not self.depth:
            self.out.append(d)


def html_to_text(s):
    p = _Text()
    try:
        p.feed(s)
    except Exception:
        pass
    return re.sub(r"[ \t　]+", " ", re.sub(r"\n\s*\n+", "\n", "".join(p.out))).strip()


# ---------- URL探し（検索はURLだけ受け取る） ----------
def tavily_urls(name, extra, doms, key, post=None, n=5):
    body = json.dumps({"query": f"{name} 福岡 {extra}".strip(), "max_results": n, "search_depth": "basic",
                       "include_domains": doms, "include_raw_content": False}).encode()
    req = urllib.request.Request("https://api.tavily.com/search", data=body, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + key, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    return [x["url"] for x in d.get("results", []) if x.get("url")]       # 本文・要約は捨てる


# ---------- 決まった形の読み取り（AIを使わない） ----------
class _Tables(html.parser.HTMLParser):
    """HTMLの表を [[行のセル文字列…]…] にする。入れ子の表は外側だけ。"""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.depth, self.row, self.cell = [], 0, None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                self.tables.append([])
        elif self.depth == 1 and tag == "tr":
            self.row = []
        elif self.depth == 1 and tag in ("td", "th") and self.row is not None:
            self.cell = []
        elif tag in ("br", "p") and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag):
        if tag == "table":
            self.depth -= 1
        elif self.depth == 1 and tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
            self.cell = None
        elif self.depth == 1 and tag == "tr" and self.row is not None:
            if self.row:
                self.tables[-1].append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


NAME_COL = re.compile(r"団地名|施設名|名称|名前")
COL_ATTR = [(re.compile(r"管理戸数|戸数|面積|延長|階数|規模"), "scale"),
            (re.compile(r"建設年|建築年|竣工|築年|開設|供用開始"), "built")]


def table_facts(html_text, name, address=""):
    """見出し（団地名・管理戸数・建設年度…）のある表から、地点名と一致する行の値を機械的に読む。
    戻り値 (事実の一覧, 該当する形の表があったか)。表があって地点が見つからない／行が決まらないときは ([], True)。
    AIが隣の行の値を取り違える事故（東箱崎の昭和61年）を、構造で防ぐ。"""
    if not html_text:
        return [], False
    p = _Tables()
    try:
        p.feed(html_text)
    except Exception:
        pass
    keys = {norm(k) for k in name_keys(name)}
    found, is_table = [], False
    for tb in p.tables:
        head = next((r for r in tb if len(r) >= 3 and any(NAME_COL.search(c) for c in r)), None)
        if head is None:
            continue
        is_table = True
        ni = next(i for i, c in enumerate(head) if NAME_COL.search(c))
        for r in tb:
            if r is head or len(r) != len(head):
                continue
            if norm(r[ni]) in keys:
                found.append((head, r, ni))
    if len(found) > 1:                                  # 同名の行が複数 → 住所の区でしぼる。決まらなければ出さない
        ku = re.search(r"([^\s\d市]{1,3}区)", address or "")
        found = [x for x in found if ku and ku.group(1) in " ".join(x[1])]
    if len(found) != 1:
        return [], is_table
    head, r, ni = found[0]
    out = []
    for ci, col in enumerate(head):
        for rx, attr in COL_ATTR:
            if rx.search(col) and r[ci].strip():
                lo, hi = sorted((ni, ci))
                v = unicodedata.normalize("NFKC", r[ci]).strip()
                if attr == "scale" and "戸" in col and v.isdigit():
                    v += "戸"
                out.append({"attr": attr, "value": v, "quote": " ".join(r[lo:hi + 1]), "via": "table", "col": col})
                break
    return out, True


LV_BUILT = r"(?P<l>業務開始|開設|創設|竣工|完成|供用開始|開業)\s*[:：]?\s*(?P<v>(?:明治|大正|昭和|平成|令和)?\s?\d+年(?:\s?\d+月)?(?:\s?\d+日)?)"
LV_SCALE = r"(?P<l>敷地面積|延床面積|延べ面積|建築面積|管理戸数|戸数|延長|階数)\s*[:：]?\s*(?P<v>[\d,.]+\s?(?:m2|㎡|m²|ha|戸|棟|階|m|ｍ)?)"


def label_value_facts(text):
    """「業務開始 昭和30年6月21日」「敷地面積 120,400m2」のような『項目 値』を、そのまま拾う。
    quote は項目名＋値だけ。呼び出し側が verify（地点名の近さを含む）を通す。"""
    t = unicodedata.normalize("NFKC", text)
    out = []
    for rx, attr in ((LV_BUILT, "built"), (LV_SCALE, "scale")):
        for m in re.finditer(rx, t):
            if attr == "scale" and not re.search(r"\d", m.group("v")):
                continue
            v = m.group("v").strip()
            if attr == "scale" and "戸数" in m.group("l") and v.isdigit():
                v += "戸"
            out.append({"attr": attr, "value": v, "quote": m.group(0).strip(), "via": "label"})
    return out


# ---------- 抽出と照合 ----------
def norm(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s))


def core(n):
    n = re.sub(r"[（(].*?[）)]", "", n)
    return re.sub(r"^(福岡市立|福岡県立|市立|県立|私立|公立)", "", n).strip()


def name_keys(name):
    """本文で地点名を探すときの候補（長い順）。DBの名前と公式ページの表記の差（県営・UR都市機構などの接頭辞、
    住宅・団地・号棟などの接尾辞、空白で区切られた名前）を吸収する。2字未満は誤一致が多いので使わない。"""
    base = unicodedata.normalize("NFKC", core(name))
    keys = {base}
    for tok in base.split():
        keys.add(tok)
    for k in list(keys):
        k2 = re.sub(r"^(県営|市営|UR都市機構|都市再生機構|福岡市|福岡県)", "", k)
        k2 = re.sub(r"(\d+号棟|\d+棟|\d+)$", "", k2)
        k2 = re.sub(r"(住宅|団地|漁港|商店街)$", "", k2)
        k2 = re.sub(r"\d+号棟$", "", k2)
        keys.add(k2.strip())
    for k in list(keys):
        keys.add(k.replace("埠頭", "ふ頭"))      # 博多埠頭 ⇔ 博多ふ頭（表記ゆれ）
    return sorted((k for k in keys if len(k) >= 2), key=len, reverse=True)


def window(text, name):
    """地点名（のいずれかの表記）が出てくる周辺だけをLLMに渡す。なければ None（無関係なページ）。
    地点名が何か所にも出るとき（問い合わせ先・目次・本文）は、事実らしい語（面積・開設・戸数など）が
    いちばん多い周辺を選ぶ。最初の1か所を取ると、問い合わせ先の欄だけを読んでしまう。"""
    t = unicodedata.normalize("NFKC", text)
    best, score = None, -1
    for k in name_keys(name):
        pos = t.find(k)
        n = 0
        while pos >= 0 and n < 40:
            seg = t[max(0, pos - PRE):max(0, pos - PRE) + WINDOW]
            sc = len(FACTWORDS.findall(seg))
            if sc > score:
                best, score = seg, sc
            pos, n = t.find(k, pos + len(k)), n + 1
    return best


def place_hint(text, address):
    """住所の区・町が本文にあるか（同名の別の場所の目印）。"""
    m = re.search(r"(?:福岡県)?(?:福岡市|北九州市)?([^\s\d]{1,4}区)([^\s\d一二三四五六七八九十]{1,6}?)(?:[\d一二三四五六七八九十]|丁目|$)", address or "")
    t = norm(text)
    parts = [x for x in (m.group(1), m.group(2)) if x] if m else []
    return bool(parts) and all(norm(p) in t for p in parts)


NEAR_SHORT, NEAR_LONG, NEAR_BACK = 200, 800, 60


def near_name(text, name, q):
    """引用が地点名の出現位置の近く（名前の後ろ NEAR 字以内）にあるか。表では隣の行の値を取り違えやすいため。
    短い引用（年・数字だけ等）は他の行と偶然一致しやすいので厳しく、長い文は緩くする。"""
    t, qq = norm(text), norm(q)
    lim = NEAR_SHORT if len(qq) < 10 else NEAR_LONG
    ks = [m.start() for k in name_keys(name) for m in re.finditer(re.escape(norm(k)), t)]
    qs = [m.start() for m in re.finditer(re.escape(qq), t)]
    return any(-NEAR_BACK <= a - b <= lim for a in qs for b in ks)


def verify(fact, text, name=None):
    """引用が本文に実在し、値の数字が引用に含まれ、除外語・評判語・撮影可否がないものだけ通す。
    name があれば、引用が地点名の近くにあることも確かめる。"""
    q, v = fact.get("quote", ""), fact.get("value", "")
    if fact.get("attr") not in ATTRS or not q or not v:
        return "項目不正"
    if norm(q) not in norm(text):
        return "引用が本文にない"
    if fact.get("via") == "table":          # 表の行から機械的に読んだもの：行の特定は構造で済んでいる
        return "除外語" if EXCL.search(v + q) else ("評判・撮影可否の語" if REPUTE.search(v) or PERMIT.search(v + q) else "")
    for num in re.findall(r"\d+(?:[.,]\d+)?", unicodedata.normalize("NFKC", v)):
        if num.replace(",", "") not in unicodedata.normalize("NFKC", q).replace(",", ""):
            return "数字が引用にない"
    if EXCL.search(v + q):
        return "除外語"
    if REPUTE.search(v) or PERMIT.search(v + q):
        return "評判・撮影可否の語"
    if not set(norm(v)) <= set(norm(q)):
        return "値が引用の語でない"      # 引用が主張を支えていない（言い換え・補足・別の文の引用）。語順は問わない（「351戸」⇔「戸数 351」）
    if len(norm(q)) < MIN_QUOTE:
        return "引用が短すぎる"
    if fact["attr"] == "owner" and not (OWNER_STRONG.search(q) or OWNER_WEAK.search(q)):
        return "引用に管理者を示す語がない"
    if name and not near_name(text, name, q):
        return "引用が地点名から離れている（隣の行の値の疑い）"
    return ""


def fact_notes(name, f):
    """承認のときに人が気をつける点を備考に書く。"""
    n = []
    q = f.get("quote", "")
    if f["attr"] == "owner" and not OWNER_STRONG.search(q):
        n.append("引用は『実施主体』などの語のみ。管理者かは要確認")
    if f["attr"] == "built" and "管理" in q:
        n.append("管理開始年。建築年とは限らない")
    if f["attr"] == "scale" and re.search(r"\d,\d{2}(?!\d)", f.get("value", "")):
        n.append("数字の区切りが不自然（原文の表記のまま。誤記の可能性）")
    if f.get("via") == "table":
        n.append(f"表の列『{f.get('col')}』を機械的に読んだ")
    if re.search(r"\d+号棟|\d+棟", name) and f["attr"] in ("scale", "built", "status"):
        n.append("地点は棟、記載は団地全体の可能性")
    return n


PROMPT = """次の本文から、「{name}」（{address}）についての事実だけを抜いてください。
抜く項目：owner=管理者・所有者／built=建築年・竣工年・開設年／scale=規模（面積・階数・戸数・延長）／status=現況（稼働・閉鎖・改修・解体予定）。
規則：
- 本文に書いてあることだけ。推測しない。別の場所の話は抜かない。
- quote は本文からそのままの一文（改変しない）。value は quote の中にある語句をそのまま抜く（言い換え・補足・要約をしない）。
- 数値は、項目名や単位も quote に含める（項目名＋数値＋単位）。
- 表の行は、団地名と値が別の行に分かれていることがある。その地点の行の値だけを使い、隣の地点の値を使わない。
- 管理者(owner)は、「管理」「所有」「運営」「設置」などの語を含む文を quote にする。見出し・メニュー・地名だけの文は quote にしない。
- 評判・おすすめ・口コミ、撮影できるかどうか、事故・死亡の話は抜かない。
- 該当がなければ空の配列。
JSONだけを返す：{{"facts":[{{"attr":"owner","value":"…","quote":"…"}}]}}

本文：
{text}"""


def ollama_facts(name, address, text, url=None, model=None, post=None):
    base = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    body = json.dumps({"model": model or os.environ.get("OLLAMA_MODEL", "qwen2.5:14b"), "stream": False,
                       "format": "json", "options": {"temperature": 0},
                       "messages": [{"role": "user", "content": PROMPT.format(name=name, address=address, text=text)}]}).encode()
    req = urllib.request.Request(base + "/api/chat", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        c = json.load(r)["message"]["content"]
    try:
        return json.loads(c).get("facts", [])
    except Exception:
        return None                    # 出力がJSONでない（空とは区別して記録する）


def confidence(items):
    """items: [(sid, attr, value, domain)] → {(sid, attr, 正規化値): 異なるドメインの数}"""
    g = collections.defaultdict(set)
    for sid, attr, value, dom in items:
        g[(sid, attr, norm(value))].add(dom)
    return {k: len(v) for k, v in g.items()}


# ---------- 本体 ----------
HEAD = ["地点ID", "地点名", "候補No", "種類", "候補の文", "出典URL", "採否（採/不採/保留）", "備考", "既存材料", "取得経路", "原文の引用", "確度", "取得日"]


def run(spots, urls, doms, out, fetcher=None, extract=ollama_facts, search=tavily_urls, key=None, log=sys.stderr):
    fetcher = fetcher or Fetcher()
    today = datetime.date.today().isoformat()
    rows, items = [], []
    for sid, name, address, cat in spots:
        cand = [u for u in urls.get(sid, [])]
        if key:
            try:
                cand += search(name, cat, doms, key)
            except Exception as e:
                print(f"{sid}\t{name}\t検索失敗 {e}", file=log)
        seen = []
        for u in cand:
            if u not in seen and allowed(u, doms):
                seen.append(u)
        if not seen:
            print(f"{sid}\t{name}\t許可リスト内のURLなし", file=log)
            continue
        for u in seen[:5]:
            text, why = fetcher.text(u)
            if text is None:
                print(f"{sid}\t{name}\t{u}\t{why}", file=log)
                continue
            w = window(text, name)
            if w is None:
                print(f"{sid}\t{name}\t{u}\t地点名が本文にない", file=log)
                continue
            dom = urllib.parse.urlparse(u).hostname
            hint = place_hint(text, address) if address else None   # 住所なしは判定不能（None）
            tfacts, is_table = table_facts(getattr(fetcher, "last_html", None), name, address)
            if is_table:                      # 表のページは機械で読む。AIには読ませない（行の取り違えを避ける）
                got = tfacts
                if not got:
                    print(f"{sid}\t{name}\t{u}\t表に該当行なし（または同名の行が決まらない）", file=log)
                    continue
            else:
                got = extract(name, address, w)
                if isinstance(got, list):
                    bad_items = [x for x in got if not isinstance(x, dict)]
                    if bad_items:
                        print(f"{sid}\t{name}\t{u}\t抽出の出力に形の違う項目を捨てた：{str(bad_items)[:80]}", file=log)
                    got = [x for x in got if isinstance(x, dict)]
                elif got is not None:
                    got = None                    # リストでもない（辞書だけ返したなど）→ 読めない扱い
                if got is None:
                    print(f"{sid}\t{name}\t{u}\t抽出の出力が読めない（JSONでない）", file=log)
                    got = []
                def vkey(f):      # 「351戸」と「351」は同じ値とみなす（数字だけで比べる。数字がなければ文字で）
                    v = str(f.get("value", ""))
                    return (f.get("attr"), re.sub(r"\D", "", unicodedata.normalize("NFKC", v)) or norm(v))
                seen_v = {vkey(f) for f in got}
                lv = [f for f in label_value_facts(w) if vkey(f) not in seen_v]
                if not got and not lv:
                    print(f"{sid}\t{name}\t{u}\t事実なし（抽出が空）", file=log)
                got = got + lv
            for f in got:
                bad = verify(f, text, name)
                if bad:
                    print(f"{sid}\t{name}\t{u}\t捨てた：{bad}\t{f.get('attr')}\t値={f.get('value')}\t引用={str(f.get('quote'))[:60]}", file=log)
                    continue
                items.append((sid, f["attr"], f["value"], dom))
                rows.append([sid, name, u, f, dom, hint])
    conf = confidence(items)
    out_rows, no = [], collections.Counter()
    for sid, name, u, f, dom, hint in rows:
        no[sid] += 1
        n = conf[(sid, f["attr"], norm(f["value"]))]
        note = (["住所なし。同名の別の場所でないか人が確認"] if hint is None else
                [] if hint else ["住所の区・町が本文にない（同名の別の場所の疑い）"]) + fact_notes(name, f)
        mark = "保留" if hint is False else ""
        out_rows.append([sid, name, str(no[sid]), f["attr"], f"{ATTRS[f['attr']]}：{f['value']}", u, mark,
                         "；".join(note), "-", "web:" + dom, f["quote"].replace("\n", " "),
                         "確度高" if n >= 2 else "要確認", today])
    with open(out, "w", encoding="utf-8-sig") as fh:      # BOM付き：Excelで開いても文字化けしない
        fh.write("\t".join(HEAD) + "\n")
        for r in out_rows:
            fh.write("\t".join(str(x).replace("\t", " ") for x in r) + "\n")
    print("done", len(out_rows), file=log)
    return out_rows


def read_spots(path):
    r = list(csv.reader(open(path, encoding="utf-8-sig"), delimiter="\t", quotechar=None))
    # 地点IDが数字の行だけ（空行・PHPの警告文などが混ざっても読み飛ばす）
    return [(x[0].strip(), x[1], x[2] if len(x) > 2 else "", x[3] if len(x) > 3 else "")
            for x in r[1:] if len(x) > 1 and x[0].strip().isdigit()]


def read_urls(path):
    d = collections.defaultdict(list)
    if path:
        for x in csv.reader(open(path, encoding="utf-8-sig"), delimiter="\t", quotechar=None):
            if len(x) >= 2 and not x[0].startswith(("#", "地点")):
                d[x[0]].append(x[1])
    return d


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("spots"); ap.add_argument("out")
    ap.add_argument("--urls"); ap.add_argument("--sources", default=os.path.join(os.path.dirname(__file__), "web_sources.txt"))
    a = ap.parse_args()
    run(read_spots(a.spots), read_urls(a.urls), load_sources(a.sources), a.out, key=os.environ.get("TAVILY_API_KEY"))
