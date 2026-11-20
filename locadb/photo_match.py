"""写真候補の名前の照合（ふるい直し。順4）：日本語の名前に加え、ローマ字・英語名・Commons のカテゴリ名とも照合する

Commons の題名・説明は、日本語のほかにローマ字・英語が多い（例「Hiyoshijinja Shrine」「Ohori Park」）。日本語の名前だけの照合では
名前不一致になって取りこぼす。ここでは、地点名（と別名）をローマ字に直し、表記ゆれ（長音・ヘボン式/訓令式）をならして照合する。
AI・外部APIは使わない（pykakasi の辞書はライブラリに入っている）。名前が短い・ありふれているときは照合しない（誤りを増やさないため）。
"""
from __future__ import annotations

import re
import unicodedata

_KKS = None

# 名前の終わりの一般名詞（これを除いた部分だけでも照合する。例：日吉神社 → 日吉）
SUFFIX = re.compile(r"(神社|公園|寺|院|城跡|城址|城|駅|橋|小学校|中学校|高等学校|高校|大学|病院|市役所|区役所|庁舎|会館|センター|広場|海岸|海水浴場|港|浜|山|島|池|通り|商店街)$")
# 一般名詞だけ・ありふれた語は照合しない
# 一般名詞だけ・ありふれた語は照合しない（ならした形で持つ）
COMMON = {"nishi", "higashi", "minami", "kita", "chuo", "naka", "shimin", "fukuoka", "hakata", "tenjin", "fukuokashi", "hakataku"}

COMMON_FOLDED: set[str] = set()


def _kks():
    global _KKS
    if _KKS is None:
        import pykakasi
        _KKS = pykakasi.kakasi()
    return _KKS


def fold(s: str) -> str:
    """ローマ字の表記ゆれをならす：長音（ō・ou・oo）、ヘボン式と訓令式（shi/si・chi/ti・tsu/tu・fu/hu・ji/zi）、記号・空白を除く"""
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))           # ō → o
    s = re.sub(r"[^a-z]", "", s)
    for a, b in (("sh", "s"), ("ch", "t"), ("ts", "t"), ("j", "z"), ("fu", "hu"), ("ou", "o"), ("oo", "o"), ("uu", "u"), ("tt", "t"), ("ss", "s"),
                 ("kk", "k"), ("pp", "p"), ("nn", "n")):
        s = s.replace(a, b)
    return s.translate(str.maketrans("dgbz", "tkps"))       # 連濁（櫛田：kushita/kushida）をならす。鍵は5文字以上なので誤りは増えにくい


COMMON_FOLDED.update(fold(x) for x in COMMON)


def romaji(name: str) -> str:
    """日本語の名前 → ならしたローマ字。ローマ字・英語はそのままならすだけ"""
    if not re.search(r"[぀-ヿ㐀-鿿]", name or ""):
        return fold(name)
    return fold("".join(x["hepburn"] for x in _kks().convert(name)))


def name_keys(names: list[str]) -> list[str]:
    """照合に使うローマ字の鍵（地点名・別名とその一般名詞を除いた形）。5文字未満の鍵・ありふれた鍵は使わない（誤照合を避ける。実データでの精度は tools/photo_rescreen.py analyze で確かめる）"""
    keys = []
    for n in names:
        n = (n or "").strip()
        if not n or n.startswith("（無名）"):
            continue
        for form in {n, SUFFIX.sub("", n) if len(SUFFIX.sub("", n)) >= 2 else n}:
            k = romaji(form)
            if len(k) >= 5 and k not in COMMON_FOLDED and k not in keys:
                keys.append(k)
    return keys


def match_kind(names: list[str], title: str, desc: str, categories: list[str] | None = None) -> str | None:
    """題名・説明・カテゴリ名に地点名が含まれるか。含まれ方で返す：'ja'（日本語）／'romaji'（ローマ字・英語）／'category'（Commons のカテゴリ名）／None"""
    def norm(s):
        return re.sub(r"[\s・\-_.()（）]+", "", (s or "").lower())
    text = norm(title) + " " + norm(desc)
    for n in names:
        n = norm(n)
        if len(n) >= 2 and n in text:
            return "ja"
    keys = name_keys(names)
    if not keys:
        return None
    ft = fold(re.sub(r"^file:", "", title or "", flags=re.I)) + "|" + fold(desc)
    if any(k in ft for k in keys):
        return "romaji"
    cats = [c for c in (categories or []) if c]
    if cats and any(k in fold(c) for c in cats for k in keys):
        return "category"
    return None
