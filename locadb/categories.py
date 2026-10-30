"""地点種別（spot_categories）と、OpenStreetMap のタグから種別を判定する規則

収集条件はここだけで決める。規則を変えると RULES_VERSION が変わり、
その回の「見つからなかった地点」は「条件変更による差分」として扱われる（消失扱いにしない）。
"""
import hashlib
import inspect
import re

# ---------------------------------------------------------------------------
# 種別の定義：code -> (表示名, 景観グループ, 形, 線の間隔m)
#   形 point：1要素=1地点（面は代表点、開いた線は中点）
#   形 line ：川・線路など。名前ごとに線をつなぎ、格子に固定した位置で地点を置く
# 並び順＝1つの要素が複数の種別に当てはまったときの「主な種別」の優先順
# ---------------------------------------------------------------------------
CATEGORIES = {
    "station":             ("駅前・交通結節点", "transport", "point", 0),
    "castle":              ("城跡・石垣", "historic", "point", 0),       # 2026-10-29 B：寺社に埋もれないよう分ける
    "shrine_temple":       ("寺社", "historic", "point", 0),
    "heritage":            ("歴史的建築・遺跡", "historic", "point", 0),
    "school":              ("学校・大学", "institution", "point", 0),
    "hospital":            ("病院・福祉施設", "institution", "point", 0),
    "civic":               ("役所・ホール・図書館・文化施設", "institution", "point", 0),
    "beach":               ("砂浜・海水浴場", "beach", "point", 0),
    "fishing_port":        ("漁港", "sea", "point", 0),
    "port":                ("港湾・ふ頭・コンテナヤード", "port_industrial", "point", 0),
    "island":              ("島", "sea", "point", 0),
    "pond":                ("池・湖・ため池・お堀", "lake", "point", 0),
    "riverbank":           ("河川敷・土手", "river", "point", 0),        # 2026-10-29 A
    "park":                ("公園・庭園・緑地", "green", "point", 0),
    "hill":                ("丘陵・山道・展望地点", "green", "point", 0),
    "farmland":            ("田園・農地", "green", "point", 0),          # 2026-10-30：1ha以上の農地を800m四方に1か所（osm_source で絞る）
    "bridge_overpass":     ("橋・歩道橋", "transport", "point", 0),
    "commercial_facility": ("商業施設", "commercial", "point", 0),
    "eatery_street":       ("飲食店街・市場・屋台", "commercial", "point", 0),
    "nightlife_area":      ("飲み屋街", "commercial", "line", 0),        # 2026-10-29 A：バー・居酒屋が集まる場所（osm_source で集計して置く）
    "yatai_area":          ("屋台の集まる場所", "commercial", "line", 0),  # 2026-10-29 A：屋台が3軒以上集まる場所（同上）
    "housing_estate":      ("団地・古い集合住宅", "residential", "point", 0),
    "new_building":        ("高層ビル・ホテル", "urban_new", "point", 0),
    "factory":             ("工場", "port_industrial", "point", 0),
    "warehouse":           ("倉庫・物流施設", "port_industrial", "point", 0),
    "ruins":               ("廃墟・廃校・廃工場", "vacant", "point", 0),  # 2026-10-29 A
    "vacant":              ("閉鎖施設・空き家", "vacant", "point", 0),   # 閉店した店（disused:shop）は入れない（2026-10-29 B）
    "redevelopment_site":  ("工事中・再開発地", "vacant", "point", 0),
    # 修正提案2A（2026-10-22）：撮影の需要がある種類を足す。いずれも景観グループは既存のものを使う
    "level_crossing":      ("踏切", "rail", "point", 0),
    "underpass":           ("トンネル・地下道", "transport", "point", 0),
    "gantry_sign":         ("門型標識", "transport", "point", 0),        # 2026-10-30：福岡市の道路施設一覧から手入力で登録（自動収集の規則はなし）
    "parking_multi":       ("立体駐車場", "urban_new", "point", 0),
    "harbour_structure":   ("渡船場・フェリー乗り場", "sea", "point", 0),
    "lighthouse":          ("灯台", "sea", "point", 0),                 # 2026-10-29 B：渡船場と分ける
    "breakwater":          ("防波堤・堤防", "sea", "point", 0),          # 2026-10-29 A：名前のない防波堤も取る
    "seaside_parking":     ("海沿いの駐車場", "transport", "point", 0),   # 2026-10-29 A：海岸・砂浜から150m以内の平面駐車場（osm_source で絞る）
    "industrial_structure": ("給水塔・サイロ・タンク", "port_industrial", "point", 0),
    "disused_railway":     ("廃線跡", "vacant", "point", 0),
    "sento":               ("銭湯", "old_town", "point", 0),
    # 店舗・飲食（2026-10-22 利用者と合意）：映画・ドラマの撮影で必要になる店。景観グループは新設の「店舗・飲食」（shop）。チェーンも含める
    "restaurant":          ("飲食店", "shop", "point", 0),
    "cafe":                ("カフェ", "shop", "point", 0),
    "bar":                 ("バー・居酒屋", "shop", "point", 0),
    "fashion_shop":        ("服飾店", "shop", "point", 0),
    "bookstore":           ("書店", "shop", "point", 0),
    "zakka_shop":          ("雑貨店", "shop", "point", 0),
    "supermarket":         ("スーパー", "shop", "point", 0),
    "other_shop":          ("その他の店（パン・花・家具・美容室など）", "shop", "point", 0),
    "shotengai":           ("商店街", "old_town", "line", 150),
    "alley":               ("細街路・路地・階段", "old_town", "line", 120),
    "public_space":        ("広場・歩行者空間", "urban_new", "line", 150),
    "river":               ("河川・運河", "river", "line", 400),
    "coast":               ("海岸・護岸・干潟", "sea", "line", 400),
    "railway":             ("線路沿い", "rail", "line", 600),
    "expressway":          ("高速道路の高架", "transport", "line", 500),   # 2026-10-29 A：都市高速など高架の区間
    "slope_street":        ("坂道・階段坂", "slope", "line", 80),      # 道路×標高から検出（locadb/slopes.py）
}
PRIORITY = {code: i for i, code in enumerate(CATEGORIES)}

# 工場・倉庫に入れない名前（2026-10-29）：工業用地の中にあっても、営業所・研修所・バスの車庫などは工場・倉庫の景観ではない。
#  ただし名前に工場・倉庫・物流などがあれば工場・倉庫のまま（例：高砂倉庫 那ノ津営業所）。車両基地は工業的な景観なので残す
#  「支店」「クラブ」は物流の拠点（日本通運 福岡海運支店・九州産直クラブなど）に多いので外さない
NOT_INDUSTRIAL_NAME = re.compile(r"営業所|研修所|研修センター|社員寮|独身寮|事務所|ショールーム|"
                                 r"乗馬|クリニック|医院|歯科|保育|幼稚園|スクール|消防|警察|交番|"
                                 r"バス(?:営業所|車庫|センター|ターミナル)|自動車学校|教習所")
INDUSTRIAL_NAME = re.compile(r"工場|製作所|製造所|倉庫|物流|運輸|ロジ|貨物ターミナル|コンテナターミナル|発電所|処理場|プラント")

LANDSCAPE_GROUPS = {
    "urban_new": "新しい都市景観", "old_town": "古い街並み・路地", "commercial": "繁華街・商業",
    "residential": "住宅地・団地", "institution": "学校・病院・公共施設", "transport": "交通インフラ・駅",
    "port_industrial": "港湾・工業・倉庫", "river": "川・河川", "sea": "海・海辺", "beach": "砂浜",
    "lake": "池・湖", "green": "公園・丘陵・自然", "historic": "歴史・寺社", "vacant": "空き施設・再開発地", "slope": "坂道・高低差", "shop": "店舗・飲食", "rail": "線路沿い",
    "water": "水辺（川・海・砂浜・池のどれでも）",
}

# 店舗・飲食（タグの値 → 種別）
SHOP_AMENITY = {"restaurant": "restaurant", "fast_food": "restaurant", "food_court": "restaurant",
                "cafe": "cafe", "ice_cream": "cafe", "bar": "bar", "pub": "bar", "biergarten": "bar"}
SHOP_SHOP = {**{k: "fashion_shop" for k in ("clothes", "shoes", "boutique", "jewelry", "bag", "fashion_accessories", "fashion", "tailor", "watches")},
             "books": "bookstore",
             **{k: "zakka_shop" for k in ("variety_store", "gift", "houseware", "stationery", "general", "interior_decoration", "art", "second_hand", "antiques")},
             **{k: "supermarket" for k in ("supermarket", "greengrocer", "wholesale")},
             **{k: "other_shop" for k in ("bakery", "florist", "furniture", "hairdresser", "beauty", "music", "records")}}

MOTORWAY = {"motorway", "motorway_link", "trunk", "trunk_link"}
WALKABLE = {"footway", "pedestrian", "path", "steps", "cycleway", "living_street", "residential",
            "service", "unclassified", "tertiary", "secondary", "primary", "track"}


def _name(t):
    return t.get("name:ja") or t.get("name") or ""


def _levels(t):
    try:
        return int(float(t.get("building:levels", "0")))
    except ValueError:
        return 0


def categories_of(t: dict, osm_type: str, closed: bool) -> list[str]:
    """タグから当てはまる種別をすべて返す（優先順）。osm_type: n / w / r"""
    if not t:
        return []
    nm = _name(t)
    a, hw = t.get("amenity"), t.get("highway")
    out = []

    if ((t.get("railway") == "station" or t.get("public_transport") == "station") and nm) \
            or (t.get("aeroway") == "terminal" and nm):                  # 空港のターミナル（2026-10-30）
        out.append("station")
    if a == "place_of_worship" and nm:
        out.append("shrine_temple")
    if t.get("historic") in ("castle", "castle_wall", "fort") or (nm and re.search(r"城跡|城址|石垣", nm)
            and not t.get("natural") and a is None and not t.get("shop")):
        out.append("castle")
    if (t.get("historic") and nm) or t.get("heritage"):
        out.append("heritage")
    if a in ("school", "university", "college"):
        out.append("school")
    if (a in ("hospital", "clinic", "nursing_home") and nm) or t.get("healthcare") == "hospital":
        out.append("hospital")
    if a in ("townhall", "library", "theatre", "arts_centre", "community_centre"):
        out.append("civic")
    if t.get("natural") == "beach" or t.get("leisure") == "beach_resort":
        out.append("beach")
    if "漁港" in nm:
        out.append("fishing_port")
    if t.get("landuse") == "port" or t.get("man_made") in ("pier", "quay") or t.get("harbour"):
        if "漁港" not in nm:
            out.append("port")
    if t.get("place") in ("island", "islet"):
        out.append("island")
    if nm and (
        (t.get("natural") == "water" and t.get("water") in ("pond", "lake", "reservoir", "moat", "lagoon"))
        or t.get("landuse") == "reservoir"
        or (t.get("natural") == "water" and not t.get("water") and re.search(r"池|湖|堀", nm))
    ):
        out.append("pond")
    if nm and RIVERBANK_NAME.search(nm) and (t.get("leisure") in ("park", "pitch", "recreation_ground", "playground")
                                            or t.get("landuse") in ("grass", "recreation_ground", "meadow")):
        out.append("riverbank")
    if t.get("leisure") in ("park", "garden") and nm:
        out.append("park")
    if t.get("natural") == "peak" or t.get("tourism") == "viewpoint":
        out.append("hill")
    if osm_type != "n" and t.get("landuse") in ("farmland", "paddy"):
        out.append("farmland")                                       # 広さと間隔は osm_source で絞る

    # 橋：撮影の対象になる橋に絞る（高速道路・幹線道路の高架は除く）
    if osm_type == "w" and t.get("bridge") and t.get("bridge") != "no" and hw and hw not in MOTORWAY:
        bname = t.get("bridge:name", "")
        if nm.endswith("橋") or bname or (hw in ("footway", "pedestrian", "path", "steps", "cycleway") and nm):
            out.append("bridge_overpass")
    elif t.get("man_made") == "bridge" and (nm or t.get("bridge:name")):
        out.append("bridge_overpass")

    if t.get("shop") in ("mall", "department_store"):
        out.append("commercial_facility")
    shop_cat = SHOP_AMENITY.get(a) or SHOP_SHOP.get(t.get("shop"))
    if shop_cat and nm:                                   # 名前のない店は取らない
        out.append(shop_cat)
    if a == "marketplace" or (osm_type != "n" and re.search(r"横丁|屋台", nm)) \
            or (hw == "pedestrian" and re.search(r"市場$", nm)):          # 柳橋連合市場など（2026-10-29 B）
        out.append("eatery_street")
    if (osm_type == "w" and t.get("building") == "apartments" and re.search(r"団地|住宅|ハイツ|コーポ", nm)) \
            or (t.get("landuse") == "residential" and "団地" in nm):
        out.append("housing_estate")
    if (osm_type == "w" and t.get("building") and _levels(t) >= 20) \
            or (t.get("tourism") == "hotel" and _levels(t) >= 10):
        out.append("new_building")
    not_industrial = bool(NOT_INDUSTRIAL_NAME.search(nm)) and not INDUSTRIAL_NAME.search(nm)
    if not not_industrial and (t.get("man_made") == "works" or (t.get("landuse") == "industrial" and nm and
                                        not re.search(r"warehouse|logistics", t.get("industrial", "")))):
        out.append("factory")
    if not not_industrial and ((osm_type == "w" and t.get("building") == "warehouse" and nm)
            or (t.get("landuse") == "industrial" and re.search(r"warehouse|logistics", t.get("industrial", "")))):
        out.append("warehouse")
    # 廃墟・廃校・廃工場（2026-10-29 A）
    if t.get("building") == "ruins" or (t.get("ruins") == "yes" and not t.get("historic")) \
            or t.get("disused:amenity") in ("school", "hospital") or t.get("abandoned:amenity") in ("school", "hospital") \
            or t.get("disused:man_made") == "works" or t.get("abandoned:man_made") == "works" \
            or t.get("disused:landuse") == "industrial" or t.get("abandoned:landuse") == "industrial" \
            or (nm and RUINS_NAME.search(nm)):
        out.append("ruins")
    # 閉鎖施設：閉店した店（disused:shop）は入れない（チェーン店の閉店ばかりで映像の場所にならない。2026-10-29 B）
    if t.get("abandoned:building") or (t.get("building") and t.get("disused") == "yes" and not t.get("disused:shop")):
        out.append("vacant")
    if t.get("landuse") in ("construction", "brownfield"):
        out.append("redevelopment_site")

    # 修正提案2A：新しい種類（撮影の需要がある構造物・施設）。名前のないものが大量に出る種類は、名前か条件で絞る
    if osm_type == "n" and t.get("railway") == "level_crossing":
        out.append("level_crossing")
    if osm_type == "w" and t.get("tunnel") in ("yes", "building_passage") and (hw or t.get("railway")) and nm \
            and not ROAD_NUMBER.match(nm):
        out.append("underpass")                                   # 名前のあるトンネル・地下道だけ（道路の暗渠・番号だけの道は除く）
    if a == "parking" and t.get("parking") == "multi-storey" or t.get("building") == "parking":
        out.append("parking_multi")
    if a == "ferry_terminal" and nm:
        out.append("harbour_structure")
    if t.get("man_made") == "lighthouse":
        out.append("lighthouse")
    if osm_type != "n" and t.get("man_made") == "breakwater":       # 名前のない防波堤も取る（2026-10-29 A）
        out.append("breakwater")
    if osm_type != "n" and a == "parking" and t.get("parking") in (None, "surface") and t.get("access") not in ("private", "no"):
        out.append("seaside_parking")                                # 海の近くかどうかは osm_source で絞る
    if t.get("man_made") in ("water_tower", "silo") or (t.get("man_made") == "storage_tank" and nm):
        out.append("industrial_structure")
    if osm_type == "w" and t.get("railway") in ("abandoned", "disused") and nm:
        out.append("disused_railway")
    if a == "public_bath" and nm:
        out.append("sento")

    # ---- 線のもの（トンネル・暗渠・地下は除く。橋の上は橋として扱う） ----
    underground = t.get("tunnel") not in (None, "no") or t.get("indoor") not in (None, "no") \
        or str(t.get("layer", "0")).lstrip("-").isdigit() and int(t.get("layer", "0")) < 0 or "地下" in nm
    on_bridge = t.get("bridge") not in (None, "no")
    if osm_type == "w" and not underground:
        if hw and SHOTENGAI_NAME.search(nm) and not NOT_SHOTENGAI_NAME.search(nm):
            out.append("shotengai")
        if not on_bridge and (hw == "living_street" or t.get("service") == "alley" or hw == "steps"):
            out.append("alley")
        if not on_bridge and hw == "pedestrian" and nm and not SHOTENGAI_NAME.search(nm) \
                and not re.search(r"市場$", nm):                     # 商店街・市場はそれぞれの種別で扱う
            out.append("public_space")
        if t.get("waterway") in ("river", "canal") and nm:
            out.append("river")
        if t.get("natural") == "coastline" and nm:
            out.append("coast")
        if t.get("railway") == "rail" and nm and t.get("service") is None:
            out.append("railway")
        if on_bridge and hw in ("motorway", "trunk") and nm and not ROAD_NUMBER.match(nm):
            out.append("expressway")                                  # 高速道路の高架（2026-10-29 A）
    if t.get("natural") == "wetland" and t.get("wetland") == "tidalflat":
        out.append("coast_point")
    if t.get("man_made") == "groyne" and nm:
        out.append("coast_point")
    if t.get("natural") in ("cape", "bay") and nm:
        out.append("coast_point")
    if t.get("place") == "square":
        out.append("public_space_point")
    # 商店街（点・面）：店・事務所・医療の要素や「○○商店街事務所」などは除く（2026-10-29 B）
    if nm and SHOTENGAI_NAME.search(nm) and not NOT_SHOTENGAI_NAME.search(nm) and not hw \
            and not t.get("shop") and not t.get("office") and not t.get("healthcare") \
            and a in (None, "marketplace") and (osm_type != "w" or closed):
        out.append("shotengai_point")

    # 「〜_point」は線の種別の点版（干潟・防波堤・広場など）。種別コードは線の種別にそろえる
    out = [c.removesuffix("_point") for c in out]
    seen, res = set(), []
    for c in out:
        if c not in seen:
            seen.add(c); res.append(c)
    return sorted(res, key=PRIORITY.get)


ROAD_NUMBER = re.compile(r"^\S*?[0-9０-９]+号線$")
# 2026-10-29 A・B
SHOTENGAI_NAME = re.compile(r"商店街|商店会|銀天|名店街|アーケード")
NOT_SHOTENGAI_NAME = re.compile(r"事務所|組合|クリニック|歯科|医院|駐車場|会館|ビル$")
RIVERBANK_NAME = re.compile(r"河川敷|河畔|土手|堤防|川.*(?:緑地|広場|運動場|グラウンド)|(?:緑地|広場).*川")
RUINS_NAME = re.compile(r"廃墟|廃校|廃工場|^旧.+(?:小学校|中学校|高校|高等学校|分校|工場)(?:跡)?$")


def display_name(t: dict, category: str) -> str:
    """地点名。橋は道路名より橋名（bridge:name）を優先。市道の番号（〇〇123号線）は名前として使わない"""
    nm = _name(t)
    if category == "bridge_overpass":
        if t.get("bridge:name"):
            return t["bridge:name"]
        parts = [x.strip() for x in nm.split(";")]
        return next((x for x in parts if x.endswith("橋")), parts[0])
    if ";" in nm and category != "bridge_overpass":
        nm = next((x.strip() for x in nm.split(";") if x.strip()), "")      # 「A;B」は先頭を地点名に（残りは別名として検索に使う。sync.alt_names_from_tags）
    if ROAD_NUMBER.match(nm):
        return ""
    if category == "station" and nm and t.get("railway") in ("station", "halt") and not nm.endswith("駅") \
            and t.get("bus") != "yes":
        return nm + "駅"                              # 「大橋」→「大橋駅」（駅名で探せるように）
    return nm


# 規則のバージョン（この関数群のソースのハッシュ）
RULES_VERSION = hashlib.sha256(
    (inspect.getsource(categories_of) + repr(CATEGORIES)).encode()).hexdigest()[:12]
