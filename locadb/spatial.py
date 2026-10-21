"""空間特徴（タスクI）：地点のまわりの建物・水辺・道から、検索の材料になる数値を計算する（AIなし・費用ゼロ）

  osm_source.read が建物・車道・水辺の形を集める（Spatial）→ 自動収集の一工程（run）が地点ごとに計算して spot_spatial に入れる。
  試し実行（dry_run）では書かずに、時間と分布だけを報告する（dry_run）。

計算（すべて地点を中心にした平面座標 m。osm_source.to_m と同じ）
- 建物の密度  半径 R_BUILDING_M の円の中の建物の面積率と棟数。事実（OSMに建物が描かれている範囲で）
- 開放性・見通し  N_RAYS 方向に RAY_RANGE_M まで見て、最初に建物に当たる距離。平均÷RAY_RANGE_M が開放性、最長が見通し。推定
  （地点が建物の中にあるときは、その建物は数えない。木・塀・地形は見ない）
- 水辺までの距離  川・海岸（線）と池・砂浜（面）への最短距離。事実
- 道路幅の目安  ROAD_SNAP_M 以内の最寄りの車道。OSM の width があればそれ、なければ車線数×LANE_M、なければ道の種別の目安。
  歩道・階段・自転車道は含めない（並走する歩道が車道より近く出て、街路の幅が小さく出るのを避ける）
- 確度  周囲 R_CONF_M の建物が MIN_CONF_BUILDINGS 棟未満なら low（空き地か、建物が描かれていない地域か区別できない）

数値は固定した閾値を持たない。分布を見て高・中・低の境界を決め、仕様書に値と分布を記録する。
"""
import json
import math
import re
import time

import numpy as np
import shapely
from shapely.strtree import STRtree

from .db import connect, job

SPATIAL_VERSION = "spatial-2"        # spatial-2：名前のない海岸線・川も水辺に数える（2026-10-21）
R_BUILDING_M = 100.0
RAY_RANGE_M = 150.0
N_RAYS = 16
R_CONF_M = 300.0
MIN_CONF_BUILDINGS = 3
ROAD_SNAP_M = 30.0
LANE_M = 3.0
MIN_BUILDINGS = 20000        # 福岡市は数十万棟。これ未満なら建物が正しく読めていないとみなし、計算も書き込みもしない
MAX_DROP = 0.4               # 前回の実行より建物が4割以上減っていたら、同じく計算しない
BATCH = 500

# 道の種別ごとの幅の目安（m）。初期の仮の値（仕様書10章タスクI）。実際の街路と見比べて見直す
ROAD_CLASS_WIDTH = {
    "motorway": 14.0, "motorway_link": 6.0, "trunk": 12.0, "trunk_link": 6.0,
    "primary": 10.0, "primary_link": 6.0, "secondary": 8.0, "secondary_link": 5.5,
    "tertiary": 6.5, "tertiary_link": 5.0, "unclassified": 5.5, "residential": 5.0,
    "living_street": 4.0, "service": 3.5, "pedestrian": 4.0, "track": 3.0,
}


def parse_width(v) -> float | None:
    """OSM の width タグ（"6"・"6.5 m" など）を m で返す。読めない・極端な値は None"""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*m?\s*", str(v or ""))
    if not m:
        return None
    w = float(m.group(1))
    return w if 0.5 <= w <= 60 else None


def parse_lanes(v) -> int | None:
    m = re.fullmatch(r"\s*(\d+)\s*", str(v or ""))
    n = int(m.group(1)) if m else 0
    return n if 1 <= n <= 12 else None


def road_width(highway: str, width_tag=None, lanes_tag=None) -> tuple[float | None, str | None]:
    """(幅の目安m, 根拠 osm_width / lanes / class)"""
    w = parse_width(width_tag)
    if w:
        return w, "osm_width"
    n = parse_lanes(lanes_tag)
    if n:
        return n * LANE_M, "lanes"
    base = ROAD_CLASS_WIDTH.get(highway)
    return (base, "class") if base else (None, None)


class Spatial:
    """建物・車道・水辺の形（平面座標 m の shapely）を集め、点ごとの特徴を計算する"""

    def __init__(self):
        self._b, self._r, self._rinfo, self._w, self._wkind = [], [], [], [], []
        self.b_tree = self.r_tree = self.w_tree = None
        self.b_arr = self.r_arr = self.w_arr = None

    # ---- 集める（osm_source.read から） ----
    def add_building(self, geom) -> None:
        if geom is not None and not geom.is_empty:
            self._b.append(geom)

    def add_road(self, line, highway: str, width=None, lanes=None) -> None:
        if highway in ROAD_CLASS_WIDTH and line is not None and not line.is_empty:
            self._r.append(line)
            self._rinfo.append((highway, width, lanes))

    def add_water(self, geom, kind: str) -> None:
        if geom is not None and not geom.is_empty:
            self._w.append(geom)
            self._wkind.append(kind)

    def build(self) -> "Spatial":
        b = np.array(self._b, dtype=object) if self._b else np.array([], dtype=object)
        if len(b):                                   # 壊れた形は直す（直せないものは捨てる）。交差の計算で例外にしない
            bad = ~shapely.is_valid(b)
            if bad.any():
                b[bad] = shapely.buffer(b[bad], 0)
            b = b[~shapely.is_empty(b)]
        self.b_arr = b
        self.r_arr = np.array(self._r, dtype=object)
        w = np.array(self._w, dtype=object)
        if len(w):
            bad = ~shapely.is_valid(w)
            if bad.any():
                w[bad] = shapely.buffer(w[bad], 0)
        self.w_arr = w
        self.b_tree = STRtree(self.b_arr) if len(self.b_arr) else None
        self.r_tree = STRtree(self.r_arr) if len(self.r_arr) else None
        self.w_tree = STRtree(self.w_arr) if len(self.w_arr) else None
        self._b = []                                  # 元のリストは手放す（メモリ）
        return self

    @property
    def n_buildings(self) -> int:
        return len(self.b_arr) if self.b_arr is not None else len(self._b)

    def counts(self) -> dict:
        return {"buildings": self.n_buildings,
                "roads": len(self.r_arr) if self.r_arr is not None else len(self._r),
                "water": len(self.w_arr) if self.w_arr is not None else len(self._w)}

    # ---- 計算 ----
    def compute(self, x: float, y: float) -> dict:
        p = shapely.Point(x, y)
        out = {"building_cover_100": 0.0, "building_count_100": 0, "open_ratio": 1.0, "sight_max_m": RAY_RANGE_M,
               "water_dist_m": None, "water_kind": None, "road_class": None, "road_width_m": None,
               "road_width_src": None, "confidence": "low"}
        if self.b_tree is not None:
            circle = p.buffer(R_BUILDING_M, quad_segs=8)
            idx = self.b_tree.query(circle, predicate="intersects")
            if len(idx):
                area = float(shapely.area(shapely.intersection(self.b_arr[idx], circle)).sum())
                out["building_cover_100"] = min(area / circle.area, 1.0)
                out["building_count_100"] = int(len(idx))
            near = self.b_tree.query(p.buffer(R_CONF_M), predicate="intersects")
            out["confidence"] = "ok" if len(near) >= MIN_CONF_BUILDINGS else "low"
            out["open_ratio"], out["sight_max_m"] = self._openness(x, y, p)
        if self.w_tree is not None:
            i, d = self.w_tree.query_nearest(p, return_distance=True, all_matches=False)
            if len(i):
                out["water_dist_m"], out["water_kind"] = float(d[0]), self._wkind[int(i[0])]
        if self.r_tree is not None:
            i, d = self.r_tree.query_nearest(p, max_distance=ROAD_SNAP_M, return_distance=True, all_matches=False)
            if len(i):
                hw, wt, ln = self._rinfo[int(i[0])]
                out["road_class"] = hw
                out["road_width_m"], out["road_width_src"] = road_width(hw, wt, ln)
        return out

    def _openness(self, x: float, y: float, p) -> tuple[float, float]:
        ang = np.arange(N_RAYS) * (2 * math.pi / N_RAYS)
        ends = np.column_stack((x + RAY_RANGE_M * np.sin(ang), y + RAY_RANGE_M * np.cos(ang)))
        starts = np.tile([x, y], (N_RAYS, 1))
        rays = shapely.linestrings(np.stack([starts, ends], axis=1))
        dist = np.full(N_RAYS, RAY_RANGE_M)
        ri, bi = self.b_tree.query(rays, predicate="intersects")
        if len(ri):
            bs = self.b_arr[bi]
            keep = ~shapely.contains(bs, p)           # 地点がその建物の中なら、自分の建物として数えない
            ri, bs = ri[keep], bs[keep]
            if len(ri):
                hit = shapely.intersection(rays[ri], bs)
                d = shapely.distance(hit, p)
                d = np.where(np.isnan(d), RAY_RANGE_M, d)
                np.minimum.at(dist, ri, d)
        return float(dist.mean() / RAY_RANGE_M), float(dist.max())


# ---------------------------------------------------------------------------
# 実行（自動収集の一工程）
# ---------------------------------------------------------------------------
def targets(conn, all_: bool = False, limit: int = 20000) -> list[dict]:
    """未計算・版が変わった・計算後に動いた地点（all_ なら全件。全件のときは spot_spatial を見ない
    ＝ DB変更190の適用前でも、試し実行で時間と分布を測れる）"""
    if all_:
        return conn.execute(
            """select s.id, ST_Y(s.geom::geometry) lat, ST_X(s.geom::geometry) lon
                 from spots s where s.data_status <> 'archived' order by s.id limit %s""", (limit,)).fetchall()
    return conn.execute(
        """select s.id, ST_Y(s.geom::geometry) lat, ST_X(s.geom::geometry) lon
             from spots s left join spot_spatial ss on ss.spot_id = s.id
            where s.data_status <> 'archived'
              and (%s or ss.spot_id is null or ss.spatial_version <> %s
                   or ss.computed_at < (select max(h.at) from spot_history h
                                         where h.spot_id = s.id and h.event = 'moved'))
            order by s.id limit %s""", (all_, SPATIAL_VERSION, limit)).fetchall()


def _round(r: dict) -> dict:
    return {**r,
            "building_cover_100": round(r["building_cover_100"], 4),
            "open_ratio": round(r["open_ratio"], 4),
            "sight_max_m": round(r["sight_max_m"], 1),
            "water_dist_m": None if r["water_dist_m"] is None else round(r["water_dist_m"], 1)}


def _pct(values, qs=(0.1, 0.5, 0.9, 0.99)):
    vals = [v for v in values if v is not None]
    return [round(float(x), 3) for x in np.quantile(vals, qs)] if vals else []


def summarize(results: list[dict]) -> dict:
    """分布（高・中・低の境界を決める材料）"""
    return {
        "n": len(results),
        "confidence_low": sum(1 for r in results if r["confidence"] == "low"),
        "building_cover_100_p10_50_90_99": _pct(r["building_cover_100"] for r in results),
        "building_count_100_p10_50_90_99": _pct(r["building_count_100"] for r in results),
        "open_ratio_p10_50_90_99": _pct(r["open_ratio"] for r in results),
        "sight_max_m_p10_50_90_99": _pct(r["sight_max_m"] for r in results),
        "water_dist_m_p10_50_90_99": _pct(r["water_dist_m"] for r in results),
        "road_found": sum(1 for r in results if r["road_class"]),
        "road_width_src": {s: sum(1 for r in results if r["road_width_src"] == s)
                           for s in ("osm_width", "lanes", "class")},
        "road_width_m_p10_50_90_99": _pct(r["road_width_m"] for r in results),
    }


def _enough(conn, counts: dict, min_buildings: int) -> str | None:
    """建物が正しく読めていないときの理由（None なら進めてよい）。取得の不具合で全地点が「建物なし」にならないための安全装置"""
    n = counts["buildings"]
    if n < min_buildings:
        return f"建物が少なすぎます（{n}棟。{min_buildings}棟未満）。読み取りの不具合の疑いがあるため、計算しません"
    prev = conn.execute(
        """select (stats->>'buildings')::int n from job_runs
            where job = 'spatial' and error is null and stats ? 'buildings' and not (stats ? 'skipped')
            order by id desc limit 1""").fetchone()
    if prev and prev["n"] and n < prev["n"] * (1 - MAX_DROP):
        return f"建物が前回（{prev['n']}棟）から大きく減っています（{n}棟）。読み取りの不具合の疑いがあるため、計算しません"
    return None


def _compute_rows(index: Spatial, rows: list[dict], on_batch=None) -> list[tuple[int, dict]]:
    from . import osm_source
    res = []
    for i in range(0, len(rows), BATCH):
        part = []
        for r in rows[i:i + BATCH]:
            x, y = osm_source.to_m(r["lon"], r["lat"])
            part.append((r["id"], _round(index.compute(x, y))))
        res.extend(part)
        if on_batch:
            on_batch(part, min(i + BATCH, len(rows)), len(rows))
    return res


def run(run_id: int | None, index: Spatial, all_: bool = False, limit: int = 20000,
        min_buildings: int | None = None) -> dict:
    """本番：対象の地点を計算して spot_spatial に入れる"""
    min_b = MIN_BUILDINGS if min_buildings is None else min_buildings
    with connect() as conn, job(conn, "spatial", run_id) as stats:
        t0 = time.time()
        stats.update(index.counts(), version=SPATIAL_VERSION)
        why = _enough(conn, stats, min_b)
        if why:
            stats["skipped"] = why
            print(f"    空間特徴：{why}")
            return stats
        rows = targets(conn, all_, limit)
        stats["target"] = len(rows)

        def save(part, done, total):
            with conn.cursor() as cur:
                cur.executemany(
                    """insert into spot_spatial (spot_id, spatial_version, computed_at, building_cover_100,
                              building_count_100, open_ratio, sight_max_m, water_dist_m, water_kind,
                              road_class, road_width_m, road_width_src, confidence)
                       values (%s,%s,now(),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       on conflict (spot_id) do update set spatial_version=excluded.spatial_version,
                         computed_at=now(), building_cover_100=excluded.building_cover_100,
                         building_count_100=excluded.building_count_100, open_ratio=excluded.open_ratio,
                         sight_max_m=excluded.sight_max_m, water_dist_m=excluded.water_dist_m,
                         water_kind=excluded.water_kind, road_class=excluded.road_class,
                         road_width_m=excluded.road_width_m, road_width_src=excluded.road_width_src,
                         confidence=excluded.confidence""",
                    [(sid, SPATIAL_VERSION, r["building_cover_100"], r["building_count_100"], r["open_ratio"],
                      r["sight_max_m"], r["water_dist_m"], r["water_kind"], r["road_class"], r["road_width_m"],
                      r["road_width_src"], r["confidence"]) for sid, r in part])
            conn.commit()
            print(f"    空間特徴 {done}/{total}", flush=True)

        results = _compute_rows(index, rows, save)
        stats["computed"] = len(results)
        stats["seconds"] = round(time.time() - t0, 1)
        stats["distribution"] = summarize([r for _, r in results])
        print(f"    空間特徴を入れた地点 {len(results)}（{stats['seconds']}秒）")
        print(f"    分布 {json.dumps(stats['distribution'], ensure_ascii=False)}")
    return stats


def dry_run(index: Spatial, min_buildings: int | None = None) -> dict:
    """試し：何も書かず、全地点を計算して時間と分布を報告する（閾値を決める材料）"""
    min_b = MIN_BUILDINGS if min_buildings is None else min_buildings
    t0 = time.time()
    stats = {**index.counts(), "version": SPATIAL_VERSION, "dry_run": True}
    with connect() as conn:
        why = _enough(conn, stats, min_b)
        if why:
            stats["skipped"] = why
            print(f"    空間特徴（試し）：{why}")
            return stats
        rows = targets(conn, True, 100000)
    stats["target"] = len(rows)
    results = _compute_rows(index, rows)
    stats["compute_seconds"] = round(time.time() - t0, 1)
    stats["distribution"] = summarize([r for _, r in results])
    print(f"    空間特徴（試し・書き込みなし）{len(results)}地点 {stats['compute_seconds']}秒")
    print(f"    分布 {json.dumps(stats['distribution'], ensure_ascii=False)}")
    return stats
