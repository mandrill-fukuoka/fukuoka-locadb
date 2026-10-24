"""境内・敷地内の地点を親子でまとめる（DB変更208。2026-10-24 利用者と合意）

寺社の境内（本殿・摂社・末社・碑など）、大きな公園・大学の敷地の中にある地点に「親」を付ける。
地点は消さない（対象外にしない）。人が付け外しした親（parent_source = manual / none）は自動では変えない。

- 親になる形（容れ物）：寺社の敷地（大きさを問わない）、公園・庭園・緑地の面（CONTAINER_MIN_M2 以上）、
  大学・短大の敷地（amenity=university / college）。OSMの閉じた線・マルチポリゴンだけ（点の要素は形がない）
- 子：容れ物の中に代表点がある地点。線の地点（川・路地・坂道など）は除く。
  中にある容れ物が複数あれば、いちばん小さいもの（直接の親）。自分より小さい容れ物には入らない
- 一覧でまとめる先（group_root_id）：親をたどって、子と同じ景観グループのいちばん上の祖先。
  同じグループの祖先がなければまとめない（例：神社の境内の池は「池」として別に出し、親だけを示す）
"""
from dataclasses import dataclass

from shapely import STRtree
from shapely.geometry import Point
from shapely.prepared import prep

from .categories import CATEGORIES

CONTAINER_MIN_M2 = {"park": 20_000}        # 公園は2ha以上だけ（小さな公園の中の遊具などは対象外のため）
CHILD_KINDS = ("point", "area", "manual")  # 線の地点（line_sample / standpoint）は子にしない
NOT_CHILD_CATS = {"underpass", "level_crossing", "station"}   # 地下のトンネル・踏切・駅は敷地の一部として扱わない
PART_NEAR_M = 50                           # 離れた飛び地（マルチポリゴンの別の部分）は、親の位置から50m以内の部分だけ使う
MIN_RATIO = 1.5                            # 容れ物は子の面の1.5倍以上の大きさのときだけ


@dataclass
class Container:
    key: str          # features.key（w123 / r456）
    cat: str          # shrine_temple / park / school
    poly: object      # 平面座標（m）の形
    area_m2: float


def container_of(key: str, cats: list[str], tags: dict, poly_m) -> Container | None:
    """OSMの面の要素が「容れ物」なら Container を返す"""
    if poly_m is None or poly_m.is_empty:
        return None
    if not poly_m.is_valid:
        poly_m = poly_m.buffer(0)
    area = float(poly_m.area)
    for c in cats:
        if c == "shrine_temple":
            return Container(key, c, poly_m, area)
        if c == "park" and area >= CONTAINER_MIN_M2["park"]:
            return Container(key, c, poly_m, area)
        if c == "school" and tags.get("amenity") in ("university", "college"):
            return Container(key, c, poly_m, area)
    return None


def group_of(cat):
    return CATEGORIES[cat][1] if cat in CATEGORIES else None


def compute(spots: list[dict], containers: list[Container], to_m) -> dict[int, int | None]:
    """spots: [{id, lon, lat, category, position_kind, features: [key...]}] → {spot_id: 直接の親の spot_id}"""
    owner = {}                                   # 容れ物の要素 → その要素を持つ地点
    for s in spots:
        for k in s["features"]:
            owner.setdefault(k, s["id"])
    pos = {s["id"]: Point(*to_m(s["lon"], s["lat"])) for s in spots}
    conts = []
    for c in containers:
        if c.key not in owner:
            continue
        poly = c.poly
        if poly.geom_type == "MultiPolygon" and len(poly.geoms) > 1:     # 例：香椎宮の形に約850m離れた頓宮の形が入っている
            at = pos[owner[c.key]]
            parts = [g for g in poly.geoms if g.distance(at) <= PART_NEAR_M] or [max(poly.geoms, key=lambda g: g.area)]
            poly = parts[0] if len(parts) == 1 else poly.__class__(parts)
            c = Container(c.key, c.cat, poly, float(poly.area))
        conts.append(c)
    own_area = {}                                # 地点自身の面の大きさ（容れ物として持つ形の最大）
    for c in conts:
        sid = owner[c.key]
        own_area[sid] = max(own_area.get(sid, 0.0), c.area_m2)
    if not conts:
        return {}
    tree = STRtree([c.poly for c in conts])
    preps = [prep(c.poly) for c in conts]
    out = {}
    for s in spots:
        if s["position_kind"] not in CHILD_KINDS or s["category"] in NOT_CHILD_CATS:
            continue
        pt = pos[s["id"]]
        best = None
        for i in tree.query(pt):
            c = conts[int(i)]
            pid = owner[c.key]
            if pid == s["id"] or c.area_m2 < own_area.get(s["id"], 0.0) * MIN_RATIO:
                continue
            if not preps[int(i)].covers(pt):
                continue
            if best is None or c.area_m2 < best[0]:
                best = (c.area_m2, pid)
        if best:
            out[s["id"]] = best[1]
    return out


def group_roots(parent: dict[int, int | None], cat: dict[int, str]) -> dict[int, int]:
    """親をたどり、子と同じ景観グループのいちばん上の祖先（一覧でまとめる先）"""
    roots = {}
    for sid, pid in parent.items():
        g, top, seen = group_of(cat.get(sid)), None, {sid}
        while pid is not None and pid not in seen:
            seen.add(pid)
            if group_of(cat.get(pid)) == g:
                top = pid
            pid = parent.get(pid)
        if top is not None:
            roots[sid] = top
    return roots


def apply(conn, snap, to_m) -> dict:
    """地点の反映の直後に呼ぶ（同じトランザクション。試し実行なら一緒に取り消される）"""
    rows = conn.execute(
        """select s.id, s.category, s.position_kind, s.parent_id, s.group_root_id, s.parent_source, s.name,
                  ST_X(s.geom::geometry) lon, ST_Y(s.geom::geometry) lat,
                  coalesce(array_agg(sf.feature_key) filter (where sf.feature_key is not null), '{}') features
             from spots s left join spot_features sf on sf.spot_id = s.id
            where s.data_status <> 'archived'
            group by s.id""").fetchall()
    spots = [dict(r) for r in rows]
    by_id = {s["id"]: s for s in spots}
    auto = compute(spots, snap.containers, to_m)
    parent = {}
    for s in spots:                               # 人が決めた親（manual）・まとめない（none）は動かさない
        if s["parent_source"] == "manual" and s["parent_id"] in by_id:
            parent[s["id"]] = s["parent_id"]
        elif s["parent_source"] != "none" and s["id"] in auto:
            parent[s["id"]] = auto[s["id"]]
    cat = {s["id"]: s["category"] for s in spots}
    roots = group_roots(parent, cat)
    updates = []
    changed = 0
    for s in spots:
        if s["parent_source"] == "none":
            continue
        pid, root = parent.get(s["id"]), roots.get(s["id"])
        src = "manual" if s["parent_source"] == "manual" and pid else ("osm" if pid else None)
        if (s["parent_id"], s["group_root_id"], s["parent_source"]) != (pid, root, src):
            updates.append((pid, root, src, s["id"]))
            changed += (s["parent_id"] != pid)
    # 対象外になった地点に残った親子は外す
    conn.execute("""update spots set parent_id=null, group_root_id=null, parent_source=null
                     where data_status = 'archived' and parent_id is not null and parent_source = 'osm'""")
    if updates:
        with conn.cursor() as cur:
            cur.executemany("update spots set parent_id=%s, group_root_id=%s, parent_source=%s where id=%s", updates)
    by_parent = {}
    for sid, pid in parent.items():
        by_parent.setdefault(pid, []).append(sid)
    top = sorted(by_parent.items(), key=lambda kv: -len(kv[1]))[:40]
    return {
        "containers": len(snap.containers), "children": len(parent), "parents": len(by_parent),
        "grouped": len(roots), "changed": changed,
        "top": [f"{by_id[p]['name']}（{len(c)}）: " + "、".join(by_id[x]["name"] for x in c[:8]) + ("…" if len(c) > 8 else "")
                for p, c in top],
        "rows": [(by_id[p]["name"], p, by_id[c]["name"], c, CATEGORIES.get(by_id[c]["category"], ("",))[0],
                  "まとめる" if c in roots else "親だけ示す")
                 for p, cs in sorted(by_parent.items(), key=lambda kv: -len(kv[1])) for c in cs],
    }
