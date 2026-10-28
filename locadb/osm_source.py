"""OpenStreetMap のスナップショット（Geofabrik の .osm.pbf）から地点の素材を取り出す

Overpass API への問い合わせは、混雑時に「空の応答」「途中で打ち切った応答」を返し、
取得失敗と本当の0件を区別できなかった。そこで九州全体のファイルを丸ごと取得し
（MD5で完全性を確認）、福岡市の範囲を切り出して手元で読む方式にした。
ファイルは「全部取れた」か「失敗した」かのどちらかになり、部分的な欠落が起きない。

  python -m locadb.osm_source --pbf fukuoka.osm.pbf   # 読み取り結果の件数だけ表示
"""
import argparse
import hashlib
import json
import math
import re
import subprocess
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import osmium
import requests
from shapely import wkb as shp_wkb
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box
import numpy as np
import shapely
from shapely.ops import linemerge, unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree

from .categories import CATEGORIES, WALKABLE, categories_of, display_name
from . import containment, slopes, spatial

GEOFABRIK_URL = "https://download.geofabrik.de/asia/japan/kyushu-latest.osm.pbf"

# 対象エリア（第1段階：福岡市7区）。osm_relation は区の行政界
AREAS = {
    "fuk-chuo":    ("中央区", 4008554),
    "fuk-hakata":  ("博多区", 4008575),
    "fuk-higashi": ("東区",   4008561),
    "fuk-sawara":  ("早良区", 4008553),
    "fuk-nishi":   ("西区",   4008548),
    "fuk-minami":  ("南区",   4008564),
    "fuk-jonan":   ("城南区", 4008545),
}
# 切り出す範囲（福岡市全域＋離島の小呂島・玄界島を含む）
CLIP_BBOX = (129.95, 33.40, 130.56, 33.92)

# 距離計算用の平面座標（福岡市付近で十分な精度。値を変えると格子の位置が変わるので固定）
LAT0, LON0 = 33.6, 130.4
K_LAT = 110_950.0
K_LON = 111_320.0 * math.cos(math.radians(LAT0))


def to_m(lon, lat, z=None):
    return ((lon - LON0) * K_LON, (lat - LAT0) * K_LAT)


def to_ll(x, y, z=None):
    return (x / K_LON + LON0, y / K_LAT + LAT0)


def geom_to_m(g):
    return shapely.transform(g, lambda c: np.column_stack(((c[:, 0] - LON0) * K_LON, (c[:, 1] - LAT0) * K_LAT)))


def norm_name(s: str) -> str:
    """名前の正規化（全半角・空白・記号のゆれを吸収）"""
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[\s・･\-‐ー－()（）「」『』]", "", s)
    s = re.sub(r"^(福岡市立|福岡県立|市立|県立|国立)", "", s)
    return s.lower()


# ---------------------------------------------------------------------------
# 取得
# ---------------------------------------------------------------------------
DOWNLOAD_ATTEMPTS = 3          # 一時的な失敗（接続・タイムアウト・5xx・転送の欠け）は、この回数まで取り直す
DOWNLOAD_WAIT_S = (30, 90)     # 取り直すまでの待ち（秒）。回数-1 個


def _download_once(dest: Path, url: str) -> dict:
    md5_text = requests.get(url + ".md5", timeout=60).text.split()[0]
    h = hashlib.md5()
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk); h.update(chunk)
    if h.hexdigest() != md5_text:
        raise RuntimeError(f"ダウンロードしたファイルが壊れています（MD5不一致 {h.hexdigest()} != {md5_text}）")
    return {"url": url, "md5": md5_text, "bytes": dest.stat().st_size}


def _retryable(e: Exception) -> bool:
    """取り直してよい失敗か。4xx（URLや権限の誤り）は取り直さない。MD5不一致（転送の欠け）は取り直す"""
    if isinstance(e, requests.HTTPError):
        code = getattr(e.response, "status_code", None)
        return code is None or code >= 500 or code == 429
    return isinstance(e, (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError,
                          RuntimeError))


def download(dest: Path, url: str = GEOFABRIK_URL, log=print) -> dict:
    """pbf と公式の MD5 を取得して照合する。一致しなければ例外（その回は中止）。
    一時的な失敗は最大 DOWNLOAD_ATTEMPTS 回まで取り直す。取り直しても取れなければ例外（既存データは変えない）"""
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            info = _download_once(dest, url)
            info["attempts"] = attempt
            return info
        except Exception as e:  # noqa: BLE001
            dest.unlink(missing_ok=True)                # 途中までのファイルは使わない
            if attempt == DOWNLOAD_ATTEMPTS or not _retryable(e):
                raise
            wait = DOWNLOAD_WAIT_S[min(attempt - 1, len(DOWNLOAD_WAIT_S) - 1)]
            log(f"    取得に失敗（{attempt}/{DOWNLOAD_ATTEMPTS} 回目）: {e!r}。{wait}秒後に取り直します")
            time.sleep(wait)


def clip(src: Path, dest: Path) -> None:
    """福岡市の範囲だけを切り出す（osmium-tool）"""
    b = ",".join(str(v) for v in CLIP_BBOX)
    subprocess.run(["osmium", "extract", "-b", b, "--strategy", "complete_ways", "-O", "-o", str(dest), str(src)],
                   check=True)


def file_timestamp(path: Path) -> str | None:
    """ファイルのデータ時点（ヘッダーの取り込み時刻。なければ最も新しい編集の時刻）"""
    for args in (["-g", "header.option.osmosis_replication_timestamp"], ["-e", "-g", "data.timestamp.last"]):
        try:
            out = subprocess.run(["osmium", "fileinfo", *args, str(path)],
                                 capture_output=True, text=True, check=True).stdout.strip()
            if out:
                return out
        except Exception:  # noqa: BLE001
            pass
    return None


# ---------------------------------------------------------------------------
# 読み取り
# ---------------------------------------------------------------------------
@dataclass
class PointFeature:
    key: str                 # n123 / w123 / r123
    osm_type: str
    osm_id: int
    name: str
    cats: list[str]
    tags: dict
    lon: float
    lat: float
    extent_m: float = 0.0    # 面の大きさ（半径相当m）。点は0
    area_code: str | None = None


@dataclass
class LineSample:
    key: str                 # 例 river:那珂川#400:12_-3（格子に固定した位置）
    line_key: str            # 例 river:那珂川
    cat: str
    name: str
    lon: float               # 地点の位置（立てる場所に寄せた後）
    lat: float
    line_lon: float          # 線上の位置
    line_lat: float
    position_kind: str       # line_sample / standpoint
    way_ids: list[int] = field(default_factory=list)
    area_code: str | None = None
    attrs: dict | None = None    # 坂道：長さ・勾配・高低差・上りの方角


@dataclass
class Snapshot:
    areas: dict              # code -> {"name", "wkt"}
    points: list[PointFeature]
    samples: list[LineSample]
    meta: dict
    present: set = field(default_factory=set)   # DBにある要素のうち、ファイルに存在したもの（条件に合わなくても）
    spatial: object = None                      # 建物・車道・水辺の形（空間特徴の計算用。spatial.Spatial）
    containers: list = field(default_factory=list)   # 境内・敷地の形（containment.Container。中の地点を親子でまとめる）


class _Reader(osmium.SimpleHandler):
    def __init__(self, ward_ids: set[int], watch: set[str]):
        super().__init__()
        self.ward_ids = ward_ids
        self.watch, self.present = watch, set()
        self.wards: dict[int, object] = {}
        self.points: list[PointFeature] = []
        self.lines: dict[tuple[str, str], list] = defaultdict(list)   # (cat, 名前) -> [(way_id, [(lon,lat)...])]
        self.rail_form: dict[int, str] = {}                            # 線路の区間（way）-> 形（DB変更211）
        self.walk: list = []                                           # 立てる場所の候補（歩ける道・橋）
        self.roads: list = []                                          # 坂道の検出に使う道（slopes.Road）
        self.wkb = osmium.geom.WKBFactory()
        self.n_nodes = self.n_ways = self.n_rels = 0
        self.spatial = spatial.Spatial()                              # 建物・車道・水辺の形
        self.containers: list = []                                    # 境内・敷地の形（寺社・大きな公園・大学）

    @staticmethod
    def _tags(o) -> dict:
        return {t.k: t.v for t in o.tags}

    def node(self, n):
        self.n_nodes += 1
        if self.watch and f"n{n.id}" in self.watch:
            self.present.add(f"n{n.id}")
        if not len(n.tags):
            return
        t = self._tags(n)
        self._poi(t, Point(*to_m(n.location.lon, n.location.lat)))
        cats = categories_of(t, "n", False)
        if cats:
            self.points.append(PointFeature(f"n{n.id}", "n", n.id, display_name(t, cats[0]), cats, t,
                                            n.location.lon, n.location.lat))

    def _poi(self, t: dict, geom) -> None:
        """ロジ情報の施設（トイレ・コンビニ・駐車場）。私有・立入不可は数えない"""
        if t.get("access") in ("private", "no"):
            return
        if t.get("amenity") == "toilets":
            self.spatial.add_poi("toilet", geom)
        elif t.get("shop") == "convenience":
            self.spatial.add_poi("convenience", geom)
        elif t.get("amenity") == "parking" and t.get("parking") not in ("street_side", "lane", "on_kerb", "half_on_kerb"):
            self.spatial.add_poi("parking", geom)

    def relation(self, r):
        if self.watch and f"r{r.id}" in self.watch:
            self.present.add(f"r{r.id}")

    def way(self, w):
        self.n_ways += 1
        if self.watch and f"w{w.id}" in self.watch:
            self.present.add(f"w{w.id}")
        t = self._tags(w)
        try:
            coords = [(nd.location.lon, nd.location.lat) for nd in w.nodes]
        except osmium.InvalidLocationError:
            return
        if len(coords) < 2:
            return
        if t.get("building") and t.get("building") != "no" and w.is_closed() and len(coords) >= 4:
            self.spatial.add_building(Polygon([to_m(*c) for c in coords]))
        hw = t.get("highway")
        if hw in spatial.ROAD_CLASS_WIDTH and t.get("tunnel") in (None, "no"):
            self.spatial.add_road(LineString([to_m(*c) for c in coords]), hw, t.get("width"), t.get("lanes"))
        # 水辺までの距離（空間特徴）：名前のない海岸線・川も数える。名前のあるものは下の「線の地点」の流れで足される
        #  （これまで natural=coastline は名前のあるものしか入らず、那の津など海沿いが「150m以内に水辺なし」になっていた）
        if not t.get("name"):
            if t.get("natural") == "coastline":
                self.spatial.add_water(LineString([to_m(*c) for c in coords]), "coast")
            elif t.get("waterway") in ("river", "canal") and t.get("tunnel") in (None, "no"):
                self.spatial.add_water(LineString([to_m(*c) for c in coords]), "river")
        # ロジ（面・線の施設）と騒音源（地上の線路・滑走路）
        if t.get("amenity") == "toilets" or t.get("shop") == "convenience" or t.get("amenity") == "parking":
            ls = LineString([to_m(*c) for c in coords])
            self._poi(t, ls.representative_point() if not (w.is_closed() and len(coords) >= 4) else Polygon(ls.coords).representative_point())
        if t.get("railway") in ("rail", "light_rail", "tram") and t.get("tunnel") in (None, "no") \
                and t.get("service") is None:
            self.spatial.add_rail(LineString([to_m(*c) for c in coords]))
        if t.get("aeroway") == "runway":
            self.spatial.add_runway(LineString([to_m(*c) for c in coords]))
        if hw in WALKABLE or (t.get("bridge") and hw):
            self.walk.append(LineString([to_m(*c) for c in coords]))
        road = slopes.road_of(w.id, t, coords)
        if road:
            self.roads.append(road)
        cats = categories_of(t, "w", w.is_closed())
        if not cats:
            return
        point_cats = []
        for c in cats:
            if CATEGORIES[c][2] == "line" and not (w.is_closed() and t.get("area") == "yes"):
                self.lines[(c, display_name(t, c))].append((w.id, coords))
                if c == "railway":
                    self.rail_form[w.id] = rail_form(t)
            else:
                point_cats.append(c)
        if not point_cats:
            return
        line_m = LineString([to_m(*c) for c in coords])
        if w.is_closed() and len(coords) >= 4:
            poly = Polygon(line_m.coords)
            if not poly.is_valid:
                poly = poly.buffer(0)
            for c in point_cats:
                if c in ("pond", "beach"):
                    self.spatial.add_water(poly, c)
            rp = poly.representative_point() if not poly.is_empty else line_m.interpolate(0.5, normalized=True)
            extent = math.sqrt(max(poly.area, 0) / math.pi)
            cont = containment.container_of(f"w{w.id}", point_cats, t, poly)
            if cont:
                self.containers.append(cont)
        else:
            rp = line_m.interpolate(0.5, normalized=True)       # 橋など開いた線は中点
            extent = line_m.length / 2
        lon, lat = to_ll(rp.x, rp.y)
        self.points.append(PointFeature(f"w{w.id}", "w", w.id, display_name(t, point_cats[0]), point_cats, t,
                                        lon, lat, round(extent, 1)))

    def area(self, a):
        if a.from_way():
            return                                   # 閉じた線は way() で処理済み
        rid = a.orig_id()
        try:
            geom = shp_wkb.loads(self.wkb.create_multipolygon(a), hex=True)
        except Exception:  # noqa: BLE001
            return
        if rid in self.ward_ids:
            self.wards[rid] = geom
            return
        t = self._tags(a)
        if t.get("building") and t.get("building") != "no":
            self.spatial.add_building(geom_to_m(geom))
        cats = [c for c in categories_of(t, "r", True) if CATEGORIES[c][2] == "point" or c in ("coast",)]
        if not cats:
            return
        gm = geom_to_m(geom)
        for c in cats:
            if c in ("pond", "beach"):
                self.spatial.add_water(gm, c)
        rp = gm.representative_point()
        lon, lat = to_ll(rp.x, rp.y)
        self.n_rels += 1
        cont = containment.container_of(f"r{rid}", cats, t, gm)
        if cont:
            self.containers.append(cont)
        self.points.append(PointFeature(f"r{rid}", "r", rid, display_name(t, cats[0]), cats, t, lon, lat,
                                        round(math.sqrt(gm.area / math.pi), 1)))


def _grid_samples(line_m, spacing: float):
    """線が通る格子（spacing m 四方）ごとに、格子の中心に最も近い線上の点を1つ置く。
    線が延びた・縮んだ・向きが変わった・分割されたときも、変化のない区間の点は同じ位置・同じキーのまま"""
    parts = list(line_m.geoms) if hasattr(line_m, "geoms") else [line_m]
    cells = set()
    for p in parts:
        n = max(int(p.length // (spacing / 8)), 1)
        for k in range(n + 1):
            q = p.interpolate(k / n, normalized=True)
            cells.add((math.floor(q.x / spacing), math.floor(q.y / spacing)))
    out = []
    for cx, cy in sorted(cells):
        cell = box(cx * spacing, cy * spacing, (cx + 1) * spacing, (cy + 1) * spacing)
        piece = line_m.intersection(cell)
        if piece.is_empty or piece.length < spacing * 0.25:
            continue
        center = Point((cx + .5) * spacing, (cy + .5) * spacing)
        pt = piece.interpolate(piece.project(center)) if piece.geom_type == "LineString" else \
            min((g.interpolate(g.project(center)) for g in piece.geoms if g.length > 0),
                key=lambda g: g.distance(center))
        out.append(((cx, cy), pt))
    kept = []
    for c, pt in out:                             # 格子の角どうしで近すぎる点は間引く（順序は格子の番号で固定）
        if all(pt.distance(k[1]) >= spacing * 0.45 for k in kept):
            kept.append((c, pt))
    return kept


STANDPOINT_MAX_M = {"river": 80, "coast": 100, "railway": 60}


def rail_form(t: dict) -> str:
    """線路の形（DB変更211）：高架 elevated / 盛土 embankment / 切り通し cutting / 地上 ground"""
    if t.get("bridge") not in (None, "no"):
        return "elevated"
    if t.get("embankment") not in (None, "no"):
        return "embankment"
    if t.get("cutting") not in (None, "no"):
        return "cutting"
    return "ground"


def read(pbf: Path, watch: set[str] | None = None, detect_slopes: bool = True) -> Snapshot:
    """watch: DBにある要素のキー。ファイルに存在したかどうか（削除か、条件から外れただけか）を記録する"""
    ward_ids = {rel for _, rel in AREAS.values()}
    r = _Reader(ward_ids, watch or set())
    r.apply_file(str(pbf), locations=True, idx="flex_mem")

    missing = [code for code, (_, rel) in AREAS.items() if rel not in r.wards]
    if missing:
        raise RuntimeError(f"区の境界がファイルに見つかりません: {missing}（切り出し範囲・ファイルを確認）")
    ward_ll = {code: r.wards[rel] for code, (_, rel) in AREAS.items()}
    ward_prep = {code: prep(g) for code, g in ward_ll.items()}
    city_m = geom_to_m(unary_union(list(ward_ll.values())))

    def area_of(lon, lat):
        p = Point(lon, lat)
        for code, g in ward_prep.items():
            if g.covers(p):
                return code
        return None

    # 点・面
    points = []
    for f in sorted(r.points, key=lambda f: ("nwr".index(f.osm_type), f.osm_id)):
        f.area_code = area_of(f.lon, f.lat)
        if f.area_code:
            points.append(f)

    # 線：同じ種別・同じ名前の線をつなぎ、市の範囲で切り、格子に固定した位置で地点を置く
    walk_tree = STRtree(r.walk) if r.walk else None
    samples = []
    for (cat, name), ways in sorted(r.lines.items()):
        spacing = CATEGORIES[cat][3]
        merged = linemerge([LineString([to_m(*c) for c in cs]) for _, cs in ways])
        merged = merged.intersection(city_m)
        if merged.is_empty or merged.length == 0:
            continue
        if merged.geom_type not in ("LineString", "MultiLineString"):
            merged = MultiLineString([g for g in getattr(merged, "geoms", []) if g.geom_type == "LineString"])
            if merged.is_empty:
                continue
        if cat in ("river", "coast"):
            r.spatial.add_water(merged, cat)
        line_key = f"{cat}:{norm_name(name) or '_'}"
        way_ids = sorted(w for w, _ in ways)
        for (cx, cy), pt in _grid_samples(merged, spacing):
            stand, kind = pt, "line_sample"
            lim = STANDPOINT_MAX_M.get(cat)
            if lim and walk_tree is not None:
                idx = walk_tree.query(pt.buffer(lim))
                best = None
                for i in idx:
                    g = r.walk[i]
                    d = g.distance(pt)
                    if d <= lim and (best is None or d < best[0]):
                        best = (d, g)
                if best:
                    stand = best[1].interpolate(best[1].project(pt)); kind = "standpoint"
            lon, lat = to_ll(stand.x, stand.y)
            llon, llat = to_ll(pt.x, pt.y)
            ac = area_of(lon, lat)
            if not ac:                                   # 寄せた先が市外なら線上の位置のまま
                lon, lat, kind = llon, llat, "line_sample"
                ac = area_of(lon, lat)
            if not ac:
                continue
            attrs = None
            if cat == "railway":                         # 線路の形：格子の位置に最も近い区間のタグ（DB変更211）
                near = min(ways, key=lambda wc: LineString([to_m(*c) for c in wc[1]]).distance(pt))
                attrs = {"rail_form": r.rail_form.get(near[0], "ground")}
            samples.append(LineSample(f"{line_key}#{int(spacing)}:{cx}_{cy}", line_key, cat, name or "",
                                      lon, lat, llon, llat, kind, way_ids, ac, attrs))

    # 坂道：市内の道に沿って標高を読み、急な区間を線の地点として置く（キーは位置の格子で固定）
    city_prep = prep(unary_union(list(ward_ll.values())))
    roads = [rd for rd in r.roads
             if any(city_prep.covers(Point(*rd.coords[i])) for i in (0, len(rd.coords) // 2, -1))]
    found, slope_stats = slopes.detect(roads) if detect_slopes else ([], {"skipped": True, "incomplete": False})
    used = set()
    for sl in found:
        ac = area_of(sl.lon, sl.lat)
        if not ac:
            continue
        x, y = to_m(sl.lon, sl.lat)
        cell = f"{math.floor(x / slopes.GRID)}_{math.floor(y / slopes.GRID)}"
        key = f"{slopes.CAT}#{slopes.GRID}:{cell}"
        n = 2
        while key in used:                         # 同じ格子に2つ（60m以上離れた別の坂）→ 番号を付ける
            key = f"{slopes.CAT}#{slopes.GRID}:{cell}:{n}"; n += 1
        used.add(key)
        line_key = f"{slopes.CAT}:{norm_name(sl.name)}" if sl.name else f"{slopes.CAT}:{cell}"
        samples.append(LineSample(key, line_key, slopes.CAT, sl.name, sl.lon, sl.lat, sl.lon, sl.lat, "line_sample",
                                  sl.way_ids or [sl.way_id], ac,
                                  {"slope_len_m": sl.length_m, "slope_grade_pct": sl.grade_pct,
                                   "slope_rise_m": sl.rise_m, "slope_bearing": sl.bearing, "slope_kind": sl.kind,
                                   "highway": sl.hw}))

    counts = defaultdict(int)
    for f in points:
        for c in f.cats:
            counts[c] += 1
    for s in samples:
        counts[s.cat] += 1
    meta = {"nodes": r.n_nodes, "ways": r.n_ways, "points": len(points), "samples": len(samples),
            "by_category": dict(sorted(counts.items())), "slopes": slope_stats}
    if slope_stats["incomplete"]:
        meta["incomplete_categories"] = [slopes.CAT]       # 標高タイルが取れなかった回：坂道は消失扱いしない
    areas = {code: {"name": AREAS[code][0], "wkt": ward_ll[code].wkt} for code in AREAS}
    sp = r.spatial.build()
    meta["spatial"] = sp.counts()
    meta["containers"] = len(r.containers)
    return Snapshot(areas, points, samples, meta, r.present, sp, r.containers)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbf", required=True)
    a = ap.parse_args()
    snap = read(Path(a.pbf))
    print(json.dumps(snap.meta, ensure_ascii=False, indent=1))
