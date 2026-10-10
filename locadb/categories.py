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
    "park":                ("公園・庭園・緑地", "green", "point", 0),
    "hill":                ("丘陵・山道・展望地点", "green", "point", 0),
    "bridge_overpass":     ("橋・歩道橋", "transport", "point", 0),
    "commercial_facility": ("商業施設", "commercial", "point", 0),
    "eatery_street":       ("飲食店街・市場・屋台", "commercial", "point", 0),
    "housing_estate":      ("団地・古い集合住宅", "residential", "point", 0),
    "new_building":        ("高層ビル・ホテル", "urban_new", "point", 0),
    "factory":             ("工場", "port_industrial", "point", 0),
    "warehouse":           ("倉庫・物流施設", "port_industrial", "point", 0),
    "vacant":              ("空き店舗・閉鎖施設", "vacant", "point", 0),
    "redevelopment_site":  ("工事中・再開発地", "vacant", "point", 0),
    "shotengai":           ("商店街", "old_town", "line", 150),
    "alley":               ("細街路・路地・階段", "old_town", "line", 120),
    "public_space":        ("広場・歩行者空間", "urban_new", "line", 150),
    "river":               ("河川・運河", "river", "line", 400),
    "coast":               ("海岸・護岸・干潟", "sea", "line", 400),
    "railway":             ("線路沿い", "transport", "line", 600),
}
PRIORITY = {code: i for i, code in enumerate(CATEGORIES)}

LANDSCAPE_GROUPS = {
    "urban_new": "新しい都市景観", "old_town": "古い街並み・路地", "commercial": "繁華街・商業",
    "residential": "住宅地・団地", "institution": "学校・病院・公共施設", "transport": "交通インフラ・駅",
    "port_industrial": "港湾・工業・倉庫", "river": "川・河川", "sea": "海・海辺", "beach": "砂浜",
    "lake": "池・湖", "green": "公園・丘陵・自然", "historic": "歴史・寺社", "vacant": "空き施設・再開発地",
    "water": "水辺（川・海・砂浜・池のどれでも）",
}

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

    if (t.get("railway") == "station" or t.get("public_transport") == "station") and nm:
        out.append("station")
    if a == "place_of_worship" and nm:
        out.append("shrine_temple")
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
    if t.get("leisure") in ("park", "garden") and nm:
        out.append("park")
    if t.get("natural") == "peak" or t.get("tourism") == "viewpoint":
        out.append("hill")

    # 橋：撮影の対象になる橋に絞る（高速道路・幹線道路の高架は除く）
    if osm_type == "w" and t.get("bridge") and t.get("bridge") != "no" and hw and hw not in MOTORWAY:
        bname = t.get("bridge:name", "")
        if nm.endswith("橋") or bname or (hw in ("footway", "pedestrian", "path", "steps", "cycleway") and nm):
            out.append("bridge_overpass")
    elif t.get("man_made") == "bridge" and (nm or t.get("bridge:name")):
        out.append("bridge_overpass")

    if t.get("shop") in ("mall", "department_store"):
        out.append("commercial_facility")
    if a == "marketplace" or (osm_type != "n" and re.search(r"横丁|屋台", nm)):
        out.append("eatery_street")
    if (osm_type == "w" and t.get("building") == "apartments" and re.search(r"団地|住宅|ハイツ|コーポ", nm)) \
            or (t.get("landuse") == "residential" and "団地" in nm):
        out.append("housing_estate")
    if (osm_type == "w" and t.get("building") and _levels(t) >= 20) \
            or (t.get("tourism") == "hotel" and _levels(t) >= 10):
        out.append("new_building")
    if t.get("man_made") == "works" or (t.get("landuse") == "industrial" and nm and
                                        not re.search(r"warehouse|logistics", t.get("industrial", ""))):
        out.append("factory")
    if (osm_type == "w" and t.get("building") == "warehouse" and nm) \
            or (t.get("landuse") == "industrial" and re.search(r"warehouse|logistics", t.get("industrial", ""))):
        out.append("warehouse")
    if t.get("disused:shop") or t.get("abandoned:building") or (t.get("building") and t.get("disused") == "yes"):
        out.append("vacant")
    if t.get("landuse") in ("construction", "brownfield"):
        out.append("redevelopment_site")

    # ---- 線のもの（トンネル・暗渠・地下は除く。橋の上は橋として扱う） ----
    underground = t.get("tunnel") not in (None, "no") or t.get("indoor") not in (None, "no") \
        or str(t.get("layer", "0")).lstrip("-").isdigit() and int(t.get("layer", "0")) < 0 or "地下" in nm
    on_bridge = t.get("bridge") not in (None, "no")
    if osm_type == "w" and not underground:
        if hw and "商店街" in nm:
            out.append("shotengai")
        if not on_bridge and (hw == "living_street" or t.get("service") == "alley" or hw == "steps"):
            out.append("alley")
        if not on_bridge and hw == "pedestrian" and nm and "商店街" not in nm:   # 商店街は商店街として扱う
            out.append("public_space")
        if t.get("waterway") in ("river", "canal") and nm:
            out.append("river")
        if t.get("natural") == "coastline" and nm:
            out.append("coast")
        if t.get("railway") == "rail" and nm and t.get("service") is None:
            out.append("railway")
    if t.get("natural") == "wetland" and t.get("wetland") == "tidalflat":
        out.append("coast_point")
    if t.get("man_made") in ("breakwater", "groyne") and nm:
        out.append("coast_point")
    if t.get("natural") in ("cape", "bay") and nm:
        out.append("coast_point")
    if (t.get("place") == "square") or ("商店街" in nm and not t.get("shop") and not hw and osm_type != "w"):
        out.append("public_space_point" if t.get("place") == "square" else "shotengai_point")

    # 「〜_point」は線の種別の点版（干潟・防波堤・広場など）。種別コードは線の種別にそろえる
    out = [c.removesuffix("_point") for c in out]
    seen, res = set(), []
    for c in out:
        if c not in seen:
            seen.add(c); res.append(c)
    return sorted(res, key=PRIORITY.get)


ROAD_NUMBER = re.compile(r"^\S*?[0-9０-９]+号線$")


def display_name(t: dict, category: str) -> str:
    """地点名。橋は道路名より橋名（bridge:name）を優先。市道の番号（〇〇123号線）は名前として使わない"""
    nm = _name(t)
    if category == "bridge_overpass":
        if t.get("bridge:name"):
            return t["bridge:name"]
        parts = [x.strip() for x in nm.split(";")]
        return next((x for x in parts if x.endswith("橋")), parts[0])
    if ROAD_NUMBER.match(nm):
        return ""
    if category == "station" and nm and t.get("railway") in ("station", "halt") and not nm.endswith("駅") \
            and t.get("bus") != "yes":
        return nm + "駅"                              # 「大橋」→「大橋駅」（駅名で探せるように）
    return nm


# 規則のバージョン（この関数群のソースのハッシュ）
RULES_VERSION = hashlib.sha256(
    (inspect.getsource(categories_of) + repr(CATEGORIES)).encode()).hexdigest()[:12]
