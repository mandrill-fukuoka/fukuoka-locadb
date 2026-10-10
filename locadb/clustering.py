"""ロケーションクラスター生成ロジック（DB非依存・単体テスト可能）

考え方:
- ある地点を起点に、異なる景観グループの地点を近い順にたどり 3〜4地点の組を作る
- 景観の差（コントラスト）の合計 ÷ 所要時間 を「切り替わり速度スコア」とする
- 移動時間は直線距離 × 迂回係数 1.3 から概算（徒歩 80m/分、車 300m/分）
  ※ 実ルートの所要時間は後段で OSRM 等に置き換え可能
"""
from dataclasses import dataclass, field
from math import asin, cos, radians, sin, sqrt

DETOUR = 1.3
SPEED_M_PER_MIN = {"walk": 80.0, "car": 300.0}
MAX_TOTAL_MIN = {"walk": 15.0, "car": 15.0}
MIN_TOTAL_MIN = 3.0

# 特に「切り替わり」として価値の高い景観グループの組（企画書2-3より）
HIGH_CONTRAST = {
    frozenset({"urban_new", "old_town"}): 1.0,
    frozenset({"urban_new", "historic"}): 1.0,
    frozenset({"urban_new", "residential"}): 0.8,
    frozenset({"commercial", "river"}): 1.0,
    frozenset({"commercial", "sea"}): 1.0,
    frozenset({"urban_new", "sea"}): 0.9,
    frozenset({"urban_new", "river"}): 0.8,
    frozenset({"old_town", "river"}): 0.8,
    frozenset({"old_town", "sea"}): 0.9,
    frozenset({"beach", "urban_new"}): 1.0,
    frozenset({"beach", "residential"}): 0.9,
    frozenset({"beach", "port_industrial"}): 1.0,
    frozenset({"beach", "green"}): 0.7,
    frozenset({"river", "sea"}): 0.6,
    frozenset({"beach", "sea"}): 0.4,
    frozenset({"lake", "urban_new"}): 1.0,
    frozenset({"lake", "commercial"}): 0.9,
    frozenset({"lake", "historic"}): 0.8,
    frozenset({"lake", "residential"}): 0.8,
    frozenset({"lake", "green"}): 0.5,
    frozenset({"commercial", "old_town"}): 0.7,
    frozenset({"urban_new", "port_industrial"}): 1.0,
    frozenset({"commercial", "port_industrial"}): 0.9,
    frozenset({"residential", "port_industrial"}): 0.9,
    frozenset({"port_industrial", "sea"}): 0.6,
    frozenset({"port_industrial", "river"}): 0.8,
    frozenset({"residential", "river"}): 0.8,
    frozenset({"residential", "sea"}): 0.9,
    frozenset({"residential", "green"}): 0.9,
    frozenset({"historic", "commercial"}): 0.8,
    frozenset({"institution", "green"}): 0.7,
    frozenset({"vacant", "urban_new"}): 0.9,
}
DEFAULT_CONTRAST = 0.5
ERA_ORDER = {"historic": 0, "showa": 1, "heisei": 2, "mixed": 2, "reiwa": 3}


@dataclass
class Spot:
    id: int
    name: str
    lat: float
    lon: float
    group: str
    group_name: str
    era: str | None = None


@dataclass
class Cluster:
    spots: list[Spot]
    legs_m: list[float]
    mode: str
    minutes: float = 0.0
    score: float = 0.0
    label: str = ""
    legs_min: list[float] = field(default_factory=list)


def haversine_m(a: Spot, b: Spot) -> float:
    r = 6371000
    dlat, dlon = radians(b.lat - a.lat), radians(b.lon - a.lon)
    h = sin(dlat / 2) ** 2 + cos(radians(a.lat)) * cos(radians(b.lat)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(h))


def contrast(a: Spot, b: Spot) -> float:
    if a.group == b.group:
        return 0.0
    c = HIGH_CONTRAST.get(frozenset({a.group, b.group}), DEFAULT_CONTRAST)
    if a.era in ERA_ORDER and b.era in ERA_ORDER:
        c += 0.15 * abs(ERA_ORDER[a.era] - ERA_ORDER[b.era])  # 時代感の差
    return c


def travel_min(m: float, mode: str) -> float:
    return m * DETOUR / SPEED_M_PER_MIN[mode]


def build_from(anchor: Spot, pool: list[Spot], mode: str, max_stops: int = 4) -> Cluster | None:
    route, legs = [anchor], []
    total = 0.0
    while len(route) < max_stops:
        cur = route[-1]
        used_groups = {s.group for s in route}
        best, best_val, best_d = None, 0.0, 0.0
        for cand in pool:
            if cand.group in used_groups:
                continue
            d = haversine_m(cur, cand)
            t = travel_min(d, mode)
            if t < 0.5 or total + t > MAX_TOTAL_MIN[mode]:
                continue
            val = contrast(cur, cand) / (t + 1.0)  # 近くて差の大きい地点を優先
            if val > best_val:
                best, best_val, best_d = cand, val, d
        if not best:
            break
        route.append(best)
        legs.append(best_d)
        total += travel_min(best_d, mode)
    if len(route) < 3 or total < MIN_TOTAL_MIN:
        return None
    c = Cluster(spots=route, legs_m=legs, mode=mode)
    c.legs_min = [round(travel_min(d, mode), 1) for d in legs]
    c.minutes = round(total, 1)
    gain = sum(contrast(route[i], route[i + 1]) for i in range(len(route) - 1))
    c.score = round(gain / (c.minutes + 2.0) * 10, 2)
    c.label = " → ".join(s.group_name for s in route)
    return c


def generate(spots: list[Spot], mode: str = "walk", top: int = 50, max_overlap: float = 0.5,
             anchors: list[Spot] | None = None) -> list[Cluster]:
    """起点（anchors。省略時は全地点）ごとに候補を作り、重複の少ない上位クラスターを返す。
    途中の地点は spots 全体から選ぶので、区の境をまたぐ流れもできる"""
    radius = MAX_TOTAL_MIN[mode] * SPEED_M_PER_MIN[mode] / DETOUR
    cell = radius
    grid: dict = {}
    for s in spots:
        grid.setdefault((int(s.lat * 111000 // cell), int(s.lon * 92700 // cell)), []).append(s)
    cands = []
    for a in (anchors if anchors is not None else spots):
        cy, cx = int(a.lat * 111000 // cell), int(a.lon * 92700 // cell)
        near = [s for dy in (-1, 0, 1) for dx in (-1, 0, 1) for s in grid.get((cy + dy, cx + dx), ())]
        pool = [s for s in near if s.id != a.id and haversine_m(a, s) <= radius]
        c = build_from(a, pool, mode)
        if c:
            cands.append(c)
    cands.sort(key=lambda c: c.score, reverse=True)
    chosen: list[Cluster] = []
    for c in cands:
        ids = {s.id for s in c.spots}
        if all(len(ids & {s.id for s in k.spots}) / len(ids) <= max_overlap for k in chosen):
            chosen.append(c)
        if len(chosen) >= top:
            break
    return chosen
