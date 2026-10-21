"""登録地の自動チェック：種別・住所・説明文の下書きの正確さを機械的に照合する（AIは使わない）

- 地点・下書きは書き換えない。指摘（Finding）を一覧にして CSV と要約を出す（--dry-run は読み取りのみ）。
  --apply のときだけ、指摘を表 spot_checks に保存する（DB変更170）。見つからなくなった指摘は resolved、
  人が「問題なし」とした指摘（dismissed）は開き直さない
- 種別：名前と種別の食い違い、英語のままの名前、坂道の誤りの疑い、同じ名前の近い地点、海辺なのに標高が高い など
- 住所：住所の区と地点の区の食い違い、区の境界との食い違い、OSMの住所タグとの食い違い、
        国土地理院の逆ジオコーダーで引き直した住所との食い違い（--geocode のとき）
- 説明文（採用待ちの下書き）：中国語・ハングルの混入、（無名）の書き残し、別の区の名前、
        離れた場所の名前、距離・標高・勾配などの数値とDBの値の食い違い、書いてはいけない話題、
        出典が必要な記述、定型の書き出し、同じ文の使い回し、短すぎる文

使い方：python -m locadb.verify (--dry-run | --apply) --out verify-out [--geocode]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

WARN, INFO = "warn", "info"          # warn：誤りの疑いが強い / info：人が見て判断する

# チェックの一覧：code -> (対象, 重さ, 表示名)
CHECKS = {
    "name_category":     ("category", WARN, "名前と種別が合わない"),
    "worship_not_temple": ("category", INFO, "教会などが「寺社」に入っている"),
    "english_name":      ("category", INFO, "名前が英語のまま"),
    "multi_name":        ("category", INFO, "名前に「;」（複数の名前）"),
    "slope_road_steep":  ("category", WARN, "車道の坂で勾配25%以上（誤りの疑い）"),
    "steps_double":      ("category", INFO, "階段が「路地・階段」と「坂道」の両方にある"),
    "same_name_near":    ("category", INFO, "同じ名前の地点が100m以内にある"),
    "seaside_high":      ("category", WARN, "海辺の種別なのに標高が30m超"),
    "name_facility":     ("category", INFO, "名前が別の用途の施設に見える（クリニック・営業所・乗馬クラブなど）"),
    "trunk_road":        ("category", WARN, "幹線道路（国道・県道など）が坂道・駅の種別"),
    "address_missing":   ("address", WARN, "住所が空（陸上の地点）"),
    "address_ward":      ("address", WARN, "住所の区と地点の区が違う"),
    "address_ward_edge": ("address", INFO, "区の境の橋・川などで、住所の区と地点の区が違う"),
    "area_outside":      ("address", WARN, "地点が自分の区の境界の外にある"),
    "osm_addr_ward":     ("address", WARN, "OSMの住所タグの区と違う"),
    "address_regeo":     ("address", WARN, "座標から引き直した住所と違う"),
    "sum_foreign":       ("summary", WARN, "中国語・ハングルの混入"),
    "sum_garbled":       ("summary", WARN, "文字化け"),
    "sum_unnamed":       ("summary", WARN, "「（無名）」の書き残し"),
    "sum_short":         ("summary", WARN, "50字未満"),
    "sum_other_ward":    ("summary", WARN, "別の区の名前だけが出てくる"),
    "sum_far_place":     ("summary", INFO, "2km以上離れた地点の名前が出てくる"),
    "sum_distance":      ("summary", WARN, "書いてある距離が実際と違う"),
    "sum_number":        ("summary", WARN, "標高・勾配などの数値がDBの値と違う"),
    "sum_forbidden":     ("summary", WARN, "書いてはいけない話題（治安・営業時間・撮影の可否など）"),
    "sum_needs_source":  ("summary", INFO, "出典が必要な記述（年代・最大・有名など）"),
    "sum_template":      ("summary", INFO, "定型の書き出し"),
    "sum_duplicate":     ("summary", WARN, "ほかの地点と同じ文章"),
}


@dataclass
class Finding:
    spot_id: int
    check: str
    detail: str
    note_id: int | None = None

    @property
    def target(self):
        return CHECKS[self.check][0]

    @property
    def severity(self):
        return CHECKS[self.check][1]


# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------
WARDS = ["東区", "博多区", "中央区", "南区", "西区", "城南区", "早良区"]
WARD_RE = re.compile(r"城南区|博多区|中央区|早良区|(?<!城)南区|東区|西区")
UNNAMED = "（無名）"


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def dist_m(lat1, lon1, lat2, lon2) -> float:
    dy = (lat2 - lat1) * 111_320.0
    dx = (lon2 - lon1) * 111_320.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def ward_of_address(addr: str | None) -> str | None:
    m = re.match(r"福岡市(城南区|博多区|中央区|早良区|南区|東区|西区)", addr or "")
    return m.group(1) if m else None


def cats(s) -> set[str]:
    return {s["category"], *(s.get("extra_categories") or [])}


# ---------------------------------------------------------------------------
# 種別
# ---------------------------------------------------------------------------
# 名前の末尾 → 当てはまるはずの種別（どれか1つに入っていればよい）。点の地点だけに使う
NAME_RULES = [
    (re.compile(r"(神社|神宮|八幡宮|天満宮|大社|稲荷社?|寺|(?<![病医学容骨療養議究])院)$"), {"shrine_temple", "heritage"}, "寺社"),
    (re.compile(r"駅$"), {"station"}, "駅"),
    (re.compile(r"(公園|緑地|庭園)$"), {"park", "heritage", "hill"}, "公園"),
    (re.compile(r"(?<!桟)橋$"), {"bridge_overpass", "heritage"}, "橋"),
    (re.compile(r"(小学校|中学校|高等学校|高校|大学|学園)$"), {"school"}, "学校"),
    (re.compile(r"(病院|医院|クリニック)$"), {"hospital"}, "病院"),
    (re.compile(r"(池|湖|ため池|堀)$"), {"pond", "park"}, "池"),
    (re.compile(r"漁港$"), {"fishing_port"}, "漁港"),
    (re.compile(r"(海水浴場|ビーチ)$"), {"beach", "coast"}, "砂浜"),
]
WORSHIP_NOT_TEMPLE = re.compile(r"教会|チャーチ|モスク|ジャーミイ|聖堂|Church|church")
ENGLISH_ONLY = re.compile(r"^[A-Za-z0-9 .,'&()/\-]+$")
SEASIDE = {"beach", "coast", "fishing_port", "port"}
# 名前が景観の種別と別の用途に見える（2026-10-21 提案1）。施設の種別に入っていれば問題なし
FACILITY_NAME = re.compile(r"クリニック|歯科|医院|薬局|保育|幼稚園|乗馬|スクール|フィットネス|美容|消防|警察|交番|"
                           r"バス(?:営業所|車庫|センター|ターミナル)|営業所$")
FACILITY_OK = {"hospital", "school", "civic", "commercial_facility", "eatery_street", "new_building",
               "vacant", "redevelopment_site", "station"}
# 幹線道路の名前。坂道・駅から外す対象（駅名は「駅」で終わるので当たらない）
TRUNK_NAME = re.compile(r"^(?:福岡県道|県道|国道|主要地方道)\s*\d+|(?:国道|県道)\d+号|バイパス$|環状線$|国体道路|都市高速|高速\d*号")


def check_category(s) -> list[Finding]:
    out, nm, c = [], s["name"] or "", cats(s)
    if not nm.startswith(UNNAMED) and s["shape"] == "point" and s["origin"] == "osm":
        for rx, want, label in NAME_RULES:
            if rx.search(nm) and not (c & want):
                out.append(Finding(s["id"], "name_category", f"名前は「{label}」に見えるが種別は「{s['catname']}」"))
                break
    if nm and not nm.startswith(UNNAMED) and FACILITY_NAME.search(nm) and not (c & FACILITY_OK):
        out.append(Finding(s["id"], "name_facility", f"「{nm}」は別の用途に見えるが種別は「{s['catname']}」"))
    if nm and not nm.endswith("駅") and TRUNK_NAME.search(nm) and c & {"slope_street", "station"}:
        out.append(Finding(s["id"], "trunk_road", f"「{nm}」は幹線道路に見えるが種別は「{s['catname']}」"))
    if "shrine_temple" in c and WORSHIP_NOT_TEMPLE.search(nm):
        out.append(Finding(s["id"], "worship_not_temple", f"「{nm}」が寺社の種別"))
    if nm and not nm.startswith(UNNAMED) and ENGLISH_ONLY.match(nm) and re.search(r"[A-Za-z]{3}", nm):
        out.append(Finding(s["id"], "english_name", nm))
    if ";" in nm:
        out.append(Finding(s["id"], "multi_name", nm))
    if s["category"] == "slope_street" and s.get("slope_kind") == "road" and (s.get("slope_grade_pct") or 0) >= 25:
        out.append(Finding(s["id"], "slope_road_steep",
                           f"平均勾配 {s['slope_grade_pct']:.0f}%・長さ {s.get('slope_len_m') or 0:.0f}m"))
    if c & SEASIDE and s.get("elevation_m") is not None and float(s["elevation_m"]) > 30:
        out.append(Finding(s["id"], "seaside_high", f"標高 {float(s['elevation_m']):.0f}m（{s['catname']}）"))
    return out


# ---------------------------------------------------------------------------
# 住所
# ---------------------------------------------------------------------------
EDGE_PRONE = {"bridge_overpass", "river", "railway", "coast", "slope_street", "alley", "hill"}   # 区の境にまたがりやすい


def check_address(s) -> list[Finding]:
    out = []
    if not s["address"]:
        if s.get("terrain_status") != "no_data":
            out.append(Finding(s["id"], "address_missing", f"{s['area_name']}・{s['catname']}"))
        return out
    w = ward_of_address(s["address"])
    if w and w != s["area_name"]:
        edge = cats(s) & EDGE_PRONE
        out.append(Finding(s["id"], "address_ward_edge" if edge else "address_ward", f"住所は{w}、地点の区は{s['area_name']}（{s['address']}）"))
    if s.get("geo_area") is not None and s["geo_area"] != s["area_code"]:
        out.append(Finding(s["id"], "area_outside", f"位置は{s['geo_area_name'] or '市外'}、地点の区は{s['area_name']}"))
    tags = s.get("tags") or {}
    for k in ("addr:city", "addr:suburb", "addr:district", "addr:full"):
        v = nfkc(tags.get(k, ""))
        m = WARD_RE.search(v)
        if m and m.group(0) != s["area_name"]:
            out.append(Finding(s["id"], "osm_addr_ward", f"{k}={v}、地点の区は{s['area_name']}"))
            break
    return out


def town_key(addr: str | None) -> str:
    """住所の比較用：区と町名まで（丁目は数字の表記ゆれがあるので「丁目」より前で切る）"""
    a = nfkc(addr or "")
    return re.split(r"[0-9一二三四五六七八九十]+丁目", a)[0]


REGEO_STATS: dict = {}


def check_regeo(rows, lookup, workers=4) -> list[Finding]:
    """国土地理院の逆ジオコーダーで引き直し、保存した住所と町名まで比べる（取れなかった地点は比べない）"""
    import requests
    out = []
    targets = [r for r in rows if r["address"]]
    REGEO_STATS.update(target=len(targets), compared=0, failed=0)
    with requests.Session() as sess, ThreadPoolExecutor(max_workers=workers) as pool:
        for r, now in zip(targets, pool.map(lambda r: lookup(r["lon"], r["lat"], sess), targets)):
            REGEO_STATS["compared" if now else "failed"] += 1
            if now and town_key(now) != town_key(r["address"]):
                out.append(Finding(r["id"], "address_regeo", f"保存：{r['address']} ／ 現在：{now}"))
    return out


# ---------------------------------------------------------------------------
# 説明文の下書き
# ---------------------------------------------------------------------------
# 日本語ではまず使わない簡体字（日本語と同じ字は入れない）、中国語の句読点、ハングル
SIMPLIFIED = set("这们个说时为发对现实经动进车东门问间关开长马鸟鱼见贝页头买卖两乐书认识让话谈请读语记样么吗呢啊过还没给从并应该业产术际计设历岁华丽众优这边轻场处网电视风饭")
FOREIGN_RE = re.compile(r"[，；]|[가-힣]")          # 「，」は中国語の読点（日本語は「、」）
GARBLED = re.compile(r"\ufffd|\?{2,}|[\u0080-\u009f]")          # 文字化け（置換文字・「??」・制御文字）
FORBIDDEN = re.compile(r"治安|営業時間|定休日|混雑|撮影許可|撮影可能|撮影でき|許可不要|許可は不要|自由に撮影|口コミ|評判")
NEEDS_SOURCE = re.compile(r"[0-9０-９]{3,4}年|明治|大正|昭和[0-9０-９一二三四五六七八九十]+年|江戸時代|室町|鎌倉時代|"
                          r"創建|建立|開山|開業|竣工|築[0-9０-９]+年|創業|日本一|最古|最大|唯一|有名|人気の|名所|絶景|"
                          r"国宝|重要文化財|指定文化財")
NUM = r"(?:約|およそ|平均|は|が|で|、)*\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*"
UNIT_M = r"(m|メートル)"
NUM_CLAIMS = [
    # (正規表現, DBの列, 許容差の関数, 表示名)
    (re.compile(r"標高" + NUM + UNIT_M), "elevation_m", lambda v: max(5.0, abs(v) * 0.25), "標高"),
    (re.compile(r"勾配" + NUM + r"(%|パーセント)"), "slope_grade_pct", lambda v: 3.0, "勾配"),
    (re.compile(r"高低差" + NUM + UNIT_M), "slope_rise_m", lambda v: max(3.0, abs(v) * 0.3), "高低差"),
    (re.compile(r"(?:長さ|全長)" + NUM + UNIT_M), "slope_len_m", lambda v: max(15.0, abs(v) * 0.3), "長さ"),
]
# 地点名のすぐ後ろの距離（「〜まで約300m」「〜から徒歩5分」）。間に読点・句点・別の数値の話（標高・幅など）を挟むものは数えない
DIST_AFTER = re.compile(r"^(?P<gap>[^、。,]{0,12}?)(?:約|およそ)?\s*(?P<n>[0-9][0-9,]*(?:\.[0-9]+)?)\s*"
                        r"(?P<u>km|キロメートル|キロ|m|メートル)(?P<within>以内|圏内|の範囲|ほどの範囲)?|"
                        r"^(?P<gap2>[^、。,]{0,12}?)徒歩\s*(?:約)?\s*(?P<min>[0-9]+)\s*分")
NOT_DISTANCE = re.compile(r"標高|高さ|幅|長さ|勾配|高低差|深さ|面積|広さ|全長|直径")


GENERIC_NAME = re.compile(r"駐車場|駐輪場|公民館|集会所|児童公園|ポケットパーク|広場|商店街|バス停|公衆トイレ")


class PlaceIndex:
    """地点の名前（3字以上・無名以外）→ 位置と広がり。本文に出てくる地点名を最長一致で探す"""

    def __init__(self, rows):
        self.pos: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
        self.line: set[str] = set()          # 川・線路など：地点は線上の一部にしかないので距離を比べない
        for r in rows:
            n = r["name"] or ""
            if len(n) >= 3 and not n.startswith(UNNAMED) and not re.fullmatch(r"[A-Za-z0-9 .\-]+", n) \
                    and not GENERIC_NAME.fullmatch(nfkc(n)):
                self.pos[nfkc(n)].append((r["lat"], r["lon"], float(r.get("extent_m") or 0)))
                if r.get("shape") == "line":
                    self.line.add(nfkc(n))
        names = sorted(self.pos, key=len, reverse=True)
        self.rx = re.compile("|".join(map(re.escape, names))) if names else None

    def nearest(self, name, lat, lon) -> float:
        """いちばん近い同名の地点までの距離。公園など広い場所は、代表点から広がりの半分を引く"""
        return min(max(0.0, dist_m(lat, lon, a, o) - e / 2) for a, o, e in self.pos[name])

    def mentions(self, text):
        return list(self.rx.finditer(text)) if self.rx else []


def _num(x: str) -> float:
    return float(x.replace(",", ""))


def check_summary(s, note, places: PlaceIndex) -> list[Finding]:
    sid, nid = s["id"], note["note_id"]
    raw = note["body"] or ""
    body = nfkc(raw)
    own = nfkc(s["name"] or "")
    out = []

    def f(check, detail):
        out.append(Finding(sid, check, detail, nid))

    zh = sorted({ch for ch in raw if ch in SIMPLIFIED} | set(FOREIGN_RE.findall(raw)))
    if zh:
        f("sum_foreign", "「" + "".join(zh)[:20] + "」")
    g = GARBLED.search(raw)
    if g:
        f("sum_garbled", _around(raw, g))
    if UNNAMED in raw or "無名" in raw:
        f("sum_unnamed", "本文に「無名」")
    if len(raw.strip()) < 50:
        f("sum_short", f"{len(raw.strip())}字")

    # 住所の区（境界の橋など）に合わせて書いた場合は、別の区とはしない
    wards = {m.group(0) for m in WARD_RE.finditer(body)}
    if wards and not wards & {s["area_name"], ward_of_address(s["address"])}:
        f("sum_other_ward", f"本文：{'・'.join(sorted(wards))}／地点：{s['area_name']}")

    mentions = places.mentions(body)
    # 話題・出典のチェックは地点名の中の語（「〜創業の地」「〜治安…」など）を除いてから
    plain = body.replace(own, "＠") if own else body
    for m in mentions:
        plain = plain.replace(m.group(0), "＠")
    m = FORBIDDEN.search(plain)
    if m:
        f("sum_forbidden", _around(plain, m))
    m = NEEDS_SOURCE.search(plain)
    if m:
        f("sum_needs_source", _around(plain, m))

    for rx, col, tol, label in NUM_CLAIMS:
        for m in rx.finditer(body):
            v = s.get(col)
            if v is None:
                if col == "elevation_m":
                    f("sum_number", f"{label} {m.group(1)}m と書いているが、DBに標高がない")
                continue
            claim, real = _num(m.group(1)), float(v)
            if abs(claim - real) > tol(real):
                f("sum_number", f"{label}：本文 {m.group(1)}／DB {real:.0f}")
            break

    seen = set()
    for m in mentions:
        nm = m.group(0)
        if nm in seen or nm == own or nm in own or own in nm or nm in nfkc(s["address"] or ""):
            continue
        seen.add(nm)
        if nm in places.line:
            continue
        d = places.nearest(nm, s["lat"], s["lon"])
        dm = DIST_AFTER.match(body[m.end():])
        gap = dm and (dm.group("gap") if dm.group("n") else dm.group("gap2")) or ""
        if dm and not NOT_DISTANCE.search(gap) and not places.rx.search(gap):
            ctx = _around(body, m, 2)[:40]
            if dm.group("min"):
                claim = int(dm.group("min")) * 80.0
                wrong = d > claim * 2 + 150 or d < claim / 2 - 150
            else:
                claim = _num(dm.group("n")) * (1000 if dm.group("u")[0] in "kキ" else 1)
                # 「〜m以内」は上限として読む
                wrong = d > claim * 1.5 + 150 if dm.group("within") else (d > claim * 2 + 150 or d < claim / 2 - 150)
            if wrong:
                f("sum_distance", f"「{nm}」本文 {ctx}／実際は直線で約{_fmt(d)}")
        elif d >= 2000:
            f("sum_far_place", f"「{nm}」（約{_fmt(d)}先）")
    return out


def _around(text, m, w=12):
    e = m.end() + (w if w > 2 else 22)
    return "…" + text[max(0, m.start() - w): e] + "…"


def _fmt(d):
    return f"{d / 1000:.1f}km" if d >= 1000 else f"{round(d, -1):.0f}m"


def template_key(s, body: str) -> str:
    b = nfkc(body)
    for n in (s["name"] or "", s["area_name"] or "", (s["address"] or "").replace("福岡市", "")):
        if n:
            b = b.replace(nfkc(n), "＠")
    b = WARD_RE.sub("＠", b)
    b = re.sub(r"[0-9.,]+", "0", b)
    return b.split("。")[0][:25]


def check_summary_corpus(pairs, min_template=25) -> list[Finding]:
    """下書き全体を見るチェック：同じ書き出しが多いもの、まったく同じ文章"""
    out = []
    keys = {note["note_id"]: template_key(s, note["body"]) for s, note in pairs}
    kc = Counter(keys.values())
    bc = Counter(nfkc(note["body"]).strip() for _, note in pairs)
    for s, note in pairs:
        k = keys[note["note_id"]]
        if len(k) >= 8 and kc[k] >= min_template:
            out.append(Finding(s["id"], "sum_template", f"「{k}…」が{kc[k]}件", note["note_id"]))
        n = bc[nfkc(note["body"]).strip()]
        if n >= 2:
            out.append(Finding(s["id"], "sum_duplicate", f"同じ文章が{n}件", note["note_id"]))
    return out


# ---------------------------------------------------------------------------
# DBから読む（読み取りのみ）
# ---------------------------------------------------------------------------
SPOTS_SQL = """
select s.id, s.code, s.name, s.category, s.extra_categories, s.area_code, a.name area_name, s.address,
       s.origin, s.position_kind, s.line_key, sc.name catname, sc.shape,
       ST_Y(s.geom::geometry) lat, ST_X(s.geom::geometry) lon,
       s.elevation_m::float8 elevation_m, s.relief_m, s.terrain_status,
       s.slope_kind, s.slope_grade_pct, s.slope_rise_m, s.slope_len_m,
       ga.code geo_area, ga.name geo_area_name,
       (select max(f.extent_m) from spot_features sf join features f on f.key = sf.feature_key
         where sf.spot_id = s.id and sf.role = 'primary') extent_m,
       (select f.tags from spot_features sf join features f on f.key = sf.feature_key
         where sf.spot_id = s.id and sf.role = 'primary' and f.tags is not null limit 1) tags
  from spots s
  join spot_categories sc on sc.code = s.category
  left join areas a on a.code = s.area_code
  left join lateral (select a2.code, a2.name from areas a2
                      where a2.geom is not null and ST_Covers(a2.geom, s.geom) limit 1) ga on true
 where s.data_status <> 'archived'
 order by s.id
"""
# 境界の外の判定：区の境界がない（テストなど）ときは比べない
NOTES_SQL = """
select distinct on (n.spot_id) n.spot_id, n.id note_id, n.source, n.prompt_version, n.body
  from spot_ai_notes n join spots s on s.id = n.spot_id
 where n.status = 'draft' and s.data_status <> 'archived'
 order by n.spot_id, n.created_at desc, n.id desc
"""
PAIRS_SQL = """
select a.id a_id, b.id b_id, a.name, a.category a_cat, b.category b_cat, a.slope_kind a_kind, b.slope_kind b_kind,
       round(ST_Distance(a.geom, b.geom))::int d
  from spots a join spots b on a.id < b.id and ST_DWithin(a.geom, b.geom, %s)
 where a.data_status <> 'archived' and b.data_status <> 'archived'
   and ((a.name = b.name and a.name not like '（無名）%%' and coalesce(a.line_key, '#' || a.id) <> coalesce(b.line_key, '#' || b.id))
        or (a.category = 'slope_street' and a.slope_kind = 'steps' and b.category = 'alley')
        or (b.category = 'slope_street' and b.slope_kind = 'steps' and a.category = 'alley'))
"""


def load(conn):
    spots = conn.execute(SPOTS_SQL).fetchall()
    has_notes = conn.execute("select to_regclass('public.spot_ai_notes') is not null x").fetchone()["x"]
    notes = conn.execute(NOTES_SQL).fetchall() if has_notes else []
    pairs = conn.execute(PAIRS_SQL, (100,)).fetchall()
    return spots, notes, pairs


def check_pairs(pairs) -> list[Finding]:
    out = []
    for p in pairs:
        steps = {p["a_cat"], p["b_cat"]} == {"slope_street", "alley"}
        if steps:
            if p["d"] <= 30:
                sid = p["a_id"] if p["a_cat"] == "slope_street" else p["b_id"]
                other = p["b_id"] if sid == p["a_id"] else p["a_id"]
                out.append(Finding(sid, "steps_double", f"路地・階段の地点 id={other} と {p['d']}m"))
        else:
            out.append(Finding(p["b_id"], "same_name_near", f"「{p['name']}」id={p['a_id']} と {p['d']}m"))
    return out


def run_checks(spots, notes, pairs, regeo=None) -> list[Finding]:
    found: list[Finding] = []
    by_id = {s["id"]: s for s in spots}
    for s in spots:
        found += check_category(s)
        found += check_address(s)
    found += check_pairs(pairs)
    if regeo:
        found += regeo(spots)
    places = PlaceIndex(spots)
    sp = [(by_id[n["spot_id"]], n) for n in notes if n["spot_id"] in by_id and n["body"]]
    for s, n in sp:
        found += check_summary(s, n, places)
    found += check_summary_corpus(sp)
    # 同じ地点・同じチェック・同じ内容は1つに
    uniq, seen = [], set()
    for x in found:
        k = (x.spot_id, x.check, x.detail)
        if k not in seen:
            seen.add(k); uniq.append(x)
    return uniq


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------
def write_outputs(found, spots, notes, out_dir: Path, web_base: str) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    by_id = {s["id"]: s for s in spots}
    note_ver = {n["note_id"]: f"{n['source']}:{n['prompt_version'] or '-'}" for n in notes}
    note_body = {n["note_id"]: (n["body"] or "").replace("\n", " ")[:200] for n in notes}
    order = {c: i for i, c in enumerate(CHECKS)}
    found = sorted(found, key=lambda x: (order[x.check], by_id[x.spot_id]["code"] or ""))
    with open(out_dir / "findings.csv", "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(["対象", "重さ", "チェック", "地点コード", "地点名", "区", "種別", "住所", "内容", "下書きの版", "下書き（先頭200字）", "地点ページ"])
        for x in found:
            s = by_id[x.spot_id]
            w.writerow([x.target, x.severity, CHECKS[x.check][2], s["code"], s["name"], s["area_name"], s["catname"],
                        s["address"] or "", x.detail, note_ver.get(x.note_id, ""), note_body.get(x.note_id, ""), f"{web_base}spot.php?id={x.spot_id}"])
    with open(out_dir / "findings.json", "w", encoding="utf-8") as fp:
        json.dump([asdict(x) | {"severity": x.severity, "target": x.target} for x in found], fp, ensure_ascii=False)

    cnt = Counter(x.check for x in found)
    spots_hit = Counter()
    for t in ("category", "address", "summary"):
        spots_hit[t] = len({x.spot_id for x in found if x.target == t})
    lines = ["## 自動チェック", "",
             f"掲載中の地点 {len(spots):,} ／ 採用待ちの下書き {len(notes):,} ／ 指摘 {len(found):,} 件", "",
             (f"住所の引き直し：比べた {REGEO_STATS['compared']:,} ／ 取れなかった {REGEO_STATS['failed']:,}\n"
              if REGEO_STATS else "住所の引き直し：今回はしていない\n"),
             f"指摘のある地点：種別 {spots_hit['category']:,} ／ 住所 {spots_hit['address']:,} ／ 説明文 {spots_hit['summary']:,}", "",
             "| 対象 | 重さ | チェック | 件数 | 例 |", "|---|---|---|---:|---|"]
    tname = {"category": "種別", "address": "住所", "summary": "説明文"}
    for c, (t, sev, label) in CHECKS.items():
        ex = [x for x in found if x.check == c][:2]
        exs = " ／ ".join(f"{by_id[x.spot_id]['name'][:16]}：{x.detail[:40]}" for x in ex).replace("|", "｜")
        lines.append(f"| {tname[t]} | {'要確認' if sev == WARN else '参考'} | {label} | {cnt.get(c, 0):,} | {exs} |")
    # 下書きの版ごとの指摘率
    ver_total = Counter(f"{n['source']}:{n['prompt_version'] or '-'}" for n in notes)
    ver_hit = Counter()
    for nid in {x.note_id for x in found if x.note_id and x.severity == WARN}:
        ver_hit[note_ver[nid]] += 1
    if ver_total:
        lines += ["", "### 下書きの版ごと（「要確認」の指摘がある下書き）", "", "| 版 | 下書き | 指摘あり | 割合 |", "|---|---:|---:|---:|"]
        for v, n in sorted(ver_total.items()):
            lines.append(f"| {v} | {n:,} | {ver_hit[v]:,} | {ver_hit[v] / n:.0%} |")
    md = "\n".join(lines) + "\n"
    (out_dir / "summary.md").write_text(md, encoding="utf-8")
    return md


UPSERT_SQL = """
insert into spot_checks (spot_id, check_code, target, severity, label, detail, note_id, fkey, last_job)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (spot_id, fkey) do update
   set last_seen = now(), last_job = excluded.last_job, severity = excluded.severity, label = excluded.label,
       status    = case when spot_checks.status = 'resolved' then 'open' else spot_checks.status end,
       closed_by = case when spot_checks.status = 'resolved' then null else spot_checks.closed_by end,
       closed_at = case when spot_checks.status = 'resolved' then null else spot_checks.closed_at end
"""


def fkey(x: Finding) -> str:
    return f"{x.check}|{x.note_id or ''}|{x.detail}"[:1000]


def store(conn, found: list[Finding], ran: set[str], job_id: int) -> dict:
    """指摘を保存する（1つのトランザクション）。今回調べたチェックで見つからなくなった未対応の指摘は resolved"""
    rows = [(x.spot_id, x.check, x.target, x.severity, CHECKS[x.check][2], x.detail, x.note_id, fkey(x), job_id)
            for x in found]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, rows)
        cur.execute("""update spot_checks set status = 'resolved', closed_by = 'verify', closed_at = now()
                        where status = 'open' and check_code = any(%s) and last_job is distinct from %s""",
                    (sorted(ran), job_id))
        resolved = cur.rowcount
        n = {r["status"]: r["n"] for r in cur.execute("select status, count(*) n from spot_checks group by status").fetchall()}
    return {"saved": len(rows), "resolved_now": resolved, "open": n.get("open", 0), "dismissed": n.get("dismissed", 0)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="読み取りのみ")
    g.add_argument("--apply", action="store_true", help="指摘を spot_checks に保存する")
    ap.add_argument("--out", default="verify-out")
    ap.add_argument("--geocode", action="store_true", help="国土地理院で住所を引き直して比べる（数分かかる）")
    ap.add_argument("--web-base", default="https://fukanim.heteml.net/locadb/")
    a = ap.parse_args(argv)

    from . import address
    from .db import connect, job
    regeo = (lambda rows: check_regeo(rows, address.lookup)) if a.geocode else None
    REGEO_STATS.clear()
    with connect() as conn:
        if a.dry_run:
            conn.read_only = True                     # 試し実行はDBに書かない
            spots, notes, pairs = load(conn)
            found = run_checks(spots, notes, pairs, regeo)
        else:
            with job(conn, "verify") as stats:
                spots, notes, pairs = load(conn)
                if not spots:
                    raise SystemExit("掲載中の地点が0件のため止めました")
                found = run_checks(spots, notes, pairs, regeo)
                ran = set(CHECKS)
                # 住所を引き直していない回、または一部が取れなかった回は、引き直しの指摘を閉じない
                if not a.geocode or REGEO_STATS.get("failed"):
                    ran.discard("address_regeo")
                jid = conn.execute("select max(id) i from job_runs where job = 'verify' and finished_at is null").fetchone()["i"]
                stats.update(store(conn, found, ran, jid), spots=len(spots), drafts=len(notes), geocode=a.geocode,
                             by_check=dict(Counter(x.check for x in found)))
            print(json.dumps(stats, ensure_ascii=False))
    md = write_outputs(found, spots, notes, Path(a.out), a.web_base)
    print(md)


if __name__ == "__main__":
    main()
