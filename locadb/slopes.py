"""坂道の検出：OSMの道路・階段に沿って国土地理院の標高タイルを読み、急な区間を「坂道・階段坂」の地点にする

収集（osm_source.read）の中で動く。結果は川・商店街と同じ「線の地点」（LineSample）として反映され、
付け替え・対象外・保護・件数の異常検知などの仕組みにそのまま乗る。

- 対象の道：住宅地の道・市道・県道・生活道路・歩道・歩行者道・階段。
  山道・林道（path / track）は「丘陵・山道」があるので除く。橋・トンネル・地下・高架（layer≠0）・屋内・
  駐車場の通路・私道の乗り入れは除く（地面の標高と道の高さが違うため）
- 道に沿って STEP m ごとに標高を読み、3点の中央値でならす（道の脇の斜面の画素を拾ったぶれを抑える）
- WIN m の区間ごとの勾配が MIN_GRADE % 以上の点が MIN_LEN m 以上続く区間を「坂」とする。区間全体の平均勾配も
  MIN_GRADE % 以上であること。車の通る道で平均が MAX_ROAD_GRADE % を超えるものはデータの誤りとして除く
- 階段は、全体の高低差が MIN_STEPS_RISE m 以上のもの
- 近い坂（同じ坂が複数のwayに分かれている等）は、高低差の大きいものを残して1つにまとめる
- 地点の位置は坂の中ほど（道の上）。キーは位置の格子（GRID m）で決め、OSMで道が分割・付け替えされても変わらない
- 標高タイルの取得に1枚でも失敗した回は「不完全」とし、この種別は消失扱いしない（sync の異常検知に渡す）
"""
import math
from collections import defaultdict
from dataclasses import dataclass, field

from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge
from shapely.strtree import STRtree

from .categories import display_name
from . import terrain

CAT = "slope_street"
# 本番の反映（apply）で坂道を登録するか。試し実行（dry_run）では常に検出して候補の一覧を出す。
# 2026-10-14 利用者の判断で、初期の基準（道は10%以上が50m以上、階段は高低差5m以上）のまま全件を登録（1,619件）。
# 基準を見直すときは、試し実行の slopes.csv で件数を確かめてから定数を変える
ENABLED_IN_APPLY = True
ROAD_TYPES = {"residential", "unclassified", "tertiary", "secondary", "primary", "living_street", "service",
              "footway", "pedestrian", "steps", "cycleway"}
VEHICULAR = {"residential", "unclassified", "tertiary", "secondary", "primary", "living_street", "service"}
SKIP_SERVICE = {"parking_aisle", "driveway", "drive-through"}
STEP = 10.0
WIN = 50.0
MIN_GRADE = 10.0
MIN_LEN = 50.0
MAX_ROAD_GRADE = 30.0
MIN_STEPS_RISE = 5.0
MERGE_M = 60.0
GRID = 80


def is_candidate(t: dict) -> bool:
    hw = t.get("highway")
    if hw not in ROAD_TYPES:
        return False
    if hw == "service" and t.get("service") in SKIP_SERVICE:
        return False
    if t.get("bridge") not in (None, "no") or t.get("tunnel") not in (None, "no") or t.get("covered") == "yes":
        return False
    if t.get("indoor") not in (None, "no") or t.get("area") == "yes":
        return False
    lay = str(t.get("layer", "0"))
    if lay.lstrip("-").isdigit() and int(lay) != 0:
        return False
    return True


@dataclass
class Road:
    way_id: int
    hw: str
    name: str
    coords: list              # [(lon, lat)]
    way_ids: list = field(default_factory=list)   # つないだ道の way（1本なら空）


@dataclass
class Slope:
    way_id: int
    hw: str
    name: str
    lon: float                # 坂の中ほど（道の上）
    lat: float
    length_m: float
    rise_m: float
    grade_pct: float          # 平均勾配
    bearing: int              # 上りの方角（北0・東90）
    kind: str                 # road / steps
    way_ids: list = field(default_factory=list)


def road_of(way_id: int, t: dict, coords: list) -> Road | None:
    if not is_candidate(t) or len(coords) < 2:
        return None
    return Road(way_id, t["highway"], display_name(t, CAT), coords)


def _dist(a, b):
    """(lat, lon) 2点の距離（m）"""
    return math.hypot((b[0] - a[0]) * terrain.M_PER_DEG,
                      (b[1] - a[1]) * terrain.M_PER_DEG * math.cos(math.radians(a[0])))


def _bearing(a, b) -> int:
    dy = (b[0] - a[0]) * terrain.M_PER_DEG
    dx = (b[1] - a[1]) * terrain.M_PER_DEG * math.cos(math.radians(a[0]))
    return int(round(math.degrees(math.atan2(dx, dy)))) % 360


def densify(coords: list) -> tuple[list, list]:
    """(lon, lat) の線 → STEP m ごとの点 [(lat, lon)] と、始点からの距離 [m]"""
    pts = [(coords[0][1], coords[0][0])]
    for (o1, a1), (o2, a2) in zip(coords, coords[1:]):
        d = _dist((a1, o1), (a2, o2))
        n = max(1, int(d // STEP))
        for i in range(1, n + 1):
            pts.append((a1 + (a2 - a1) * i / n, o1 + (o2 - o1) * i / n))
    dist = [0.0]
    for p, q in zip(pts, pts[1:]):
        dist.append(dist[-1] + _dist(p, q))
    return pts, dist


def heights(tiles: "terrain.Tiles", pts: list) -> list | None:
    """1本の道は同じデータで読む（データが混ざると段差ができる）。どのデータでも欠けがあれば None"""
    for layer in terrain.LAYERS:
        need = {tiles.key(layer, a, o) for a, o in pts}
        if any(k not in tiles.cache and k not in tiles.failed for k in need):
            tiles.prefetch(need)                       # まとめて取っていないタイルだけ（単体で使うとき）
        hs = [tiles.value(layer, a, o) for a, o in pts]
        if all(h is not None for h in hs):
            return hs
    return None


def smooth(hs: list) -> list:
    """前後の点との中央値（両端はそのまま。2点の「中央値」は高い方に偏るため）"""
    out = list(hs)
    for i in range(1, len(hs) - 1):
        out[i] = sorted(hs[i - 1:i + 2])[1]
    return out


def find(road: Road, tiles: "terrain.Tiles") -> list[Slope]:
    pts, dist = densify(road.coords)
    hs = heights(tiles, pts)
    if hs is None or len(pts) < 2:
        return []
    hs = smooth(hs)
    total = dist[-1]
    if road.hw == "steps":
        rise = abs(hs[-1] - hs[0])
        if rise < MIN_STEPS_RISE:
            return []
        lo, hi = (0, len(pts) - 1) if hs[0] <= hs[-1] else (len(pts) - 1, 0)
        mid = pts[len(pts) // 2]
        return [Slope(road.way_id, road.hw, road.name, mid[1], mid[0], round(total, 1), round(rise, 1),
                      round(rise / max(total, 1.0) * 100, 1), _bearing(pts[lo], pts[hi]), "steps",
                      road.way_ids or [road.way_id])]
    if total < MIN_LEN:
        return []
    # 各点の前後 WIN/2 m での勾配
    n = len(pts)
    steep = [False] * n
    j0 = j1 = 0
    for i in range(n):
        while dist[i] - dist[j0] > WIN / 2:
            j0 += 1
        while j1 < n - 1 and dist[j1] - dist[i] < WIN / 2:
            j1 += 1
        span = dist[j1] - dist[j0]
        if span >= WIN * 0.8:
            steep[i] = abs(hs[j1] - hs[j0]) / span * 100 >= MIN_GRADE
    out = []
    i = 0
    while i < n:
        if not steep[i]:
            i += 1
            continue
        k = i
        while k + 1 < n and (steep[k + 1] or (k + 2 < n and steep[k + 2])):   # 1点の途切れは続きとみなす
            k += 1
        # 区間の端は勾配窓の半分だけ外へ広げる（窓の中心の点だけでは坂の両端が欠ける）
        a = i
        while a > 0 and dist[i] - dist[a - 1] <= WIN / 2:
            a -= 1
        b = k
        while b < n - 1 and dist[b + 1] - dist[k] <= WIN / 2:
            b += 1
        length = dist[b] - dist[a]
        rise = abs(hs[b] - hs[a])
        grade = rise / length * 100 if length > 0 else 0
        if length >= MIN_LEN and grade >= MIN_GRADE and not (road.hw in VEHICULAR and grade > MAX_ROAD_GRADE):
            lo, hi = (a, b) if hs[a] <= hs[b] else (b, a)
            m = min(range(a, b + 1), key=lambda x: abs(dist[x] - (dist[a] + dist[b]) / 2))
            out.append(Slope(road.way_id, road.hw, road.name, pts[m][1], pts[m][0], round(length, 1),
                             round(rise, 1), round(grade, 1), _bearing(pts[lo], pts[hi]), "road",
                             road.way_ids or [road.way_id]))
        i = k + 1
    return out


def merge_roads(roads: list[Road]) -> list[Road]:
    """種類・名前が同じで端どうしがつながる道を1本にする（OSMで1本の道が途中で分割されていても同じ坂になるように）。
    交差点（3本以上が集まる点）ではつながない。way の番号はつながった道の最小のもの"""
    groups = defaultdict(list)
    for r in roads:
        groups[(r.hw, r.name)].append(r)
    out = []
    for (hw, name), rs in sorted(groups.items()):
        if len(rs) == 1:
            out.append(rs[0]); continue
        lines = [LineString(r.coords) for r in rs]
        merged = linemerge(MultiLineString(lines))
        parts = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
        tree = STRtree(lines)
        for part in parts:
            members = [rs[i].way_id for i in tree.query(part) if part.buffer(1e-7).covers(lines[i])]
            out.append(Road(min(members) if members else rs[0].way_id, hw, name, list(part.coords),
                            sorted(members)))
    return out


def detect(roads: list[Road], tiles: "terrain.Tiles | None" = None) -> tuple[list[Slope], dict]:
    """道の一覧 → 坂の一覧（近いものはまとめ済み）と、実行の記録"""
    tiles = tiles or make_tiles()
    n_ways = len(roads)
    roads = merge_roads(roads)
    dens = [(r, densify(r.coords)[0]) for r in roads]
    # データを細かい順にまとめて取る。前のデータで欠けがあった道の分だけ、次のデータを取りに行く
    need, incomplete = dens, False
    for layer in terrain.LAYERS:
        if not need:
            break
        tiles.prefetch({tiles.key(layer, a, o) for _, pts in need for a, o in pts})
        nxt = []
        for r, pts in need:
            try:
                if any(tiles.value(layer, a, o) is None for a, o in pts):
                    nxt.append((r, pts))
            except terrain.FetchError:
                incomplete = True
        need = nxt
    found = []
    for r, pts in dens:
        try:
            found.extend(find(r, tiles))
        except terrain.FetchError:
            incomplete = True
    # 近い坂は1つに（高低差の大きいものを残す。同じ値なら way の番号で決める＝毎回同じ結果）
    kept: list[Slope] = []
    for s in sorted(found, key=lambda s: (-s.rise_m, s.way_id, s.lon, s.lat)):
        if all(_dist((s.lat, s.lon), (k.lat, k.lon)) >= MERGE_M for k in kept):
            kept.append(s)
    stats = {"ways": n_ways, "roads": len(roads), "found": len(found), "slopes": len(kept), "tiles": tiles.fetched,
             "tiles_failed": len(tiles.failed), "incomplete": incomplete or bool(tiles.failed)}
    return kept, stats


def make_tiles():
    """標高タイルの取得元（テストでは差し替える）"""
    return terrain.Tiles()
