"""スナップショット（osm_source.read の結果）を DB に反映する

原則（改修指示書より）
- 取得に失敗した回は、そもそもここまで来ない（ファイルの取得・照合に失敗したら中止）
- 前回より件数が大きく減った種別は「異常」とみなし、その種別では消失扱いをしない
- 人が確認した地点（ロケハン済み・確認済み・資料確認済み・現地確認済み・手入力・位置固定）は
  自動で動かさず、変化があれば「要確認」にして履歴に残す。比べる相手は「前回見たOSMの値」
  （spots.osm_seen）なので、人が直した位置や、要確認を解消した地点が毎回ぶり返すことはない
- 地点は削除しない。対象外にするときは理由と回を記録し、OSMに戻れば自動で戻す
- すべての変化は spot_history に残る。試し実行（dry_run）では最後に全部取り消し、レポートだけ残す
"""
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .categories import CATEGORIES, LANDSCAPE_GROUPS, PRIORITY, RULES_VERSION
from .osm_source import AREAS, Snapshot, norm_name, to_m

MOVE_M = 10              # これ以上動いたら「位置の変化」
MISSING_ARCHIVE_RUNS = 2  # OSMから消えた地点は、2回続けて見つからなければ対象外
DROP_ANOMALY = 0.4       # 前回より4割以上減った種別は異常（消失扱いしない）
MAX_ARCHIVE_RATIO = 0.05  # 1回で地点の5%を超えて対象外になるなら止める
CLUSTER_MOVE_M = 30


def group_of(cat):
    return CATEGORIES[cat][1] if cat in CATEGORIES else None


def dist_m(a, b):
    ax, ay = to_m(*a); bx, by = to_m(*b)
    return math.hypot(ax - bx, ay - by)


class Grid:
    """近くの地点をすばやく探すための格子"""
    def __init__(self, size=200):
        self.size, self.cells = size, defaultdict(list)

    def _c(self, ll):
        x, y = to_m(*ll)
        return int(x // self.size), int(y // self.size)

    def add(self, ll, item):
        self.cells[self._c(ll)].append((ll, item))

    def near(self, ll, r):
        cx, cy = self._c(ll); k = int(r // self.size) + 1
        for dx in range(-k, k + 1):
            for dy in range(-k, k + 1):
                for pll, item in self.cells.get((cx + dx, cy + dy), ()):
                    d = dist_m(ll, pll)
                    if d <= r:
                        yield d, item


@dataclass
class Spot:
    id: int
    code: str
    name: str
    ll: tuple
    category: str
    extra: list
    area_code: str
    data_status: str
    status: str
    confidence: str
    origin: str
    locked: bool
    missing_runs: int
    archived_reason: str | None
    sample_key: str | None
    line_key: str | None
    review_reason: str | None
    features: dict = field(default_factory=dict)   # feature_key -> role
    osm_seen: dict | None = None                    # 保護された地点：前回見たOSMの値（比較の基準）

    @property
    def protected(self):
        return (self.origin == "manual" or self.locked or self.status in ("scouted", "verified")
                or self.confidence in ("desk_checked", "field_checked"))


class Sync:
    def __init__(self, conn, snap: Snapshot, run_id: int, log=print, allow_large_change: bool = False):
        self.c, self.snap, self.run, self.log = conn, snap, run_id, log
        self.allow_large_change = allow_large_change
        self._hist, self._ids = [], []
        self.events = Counter()
        self.lists = defaultdict(list)
        self.seen_spots: set[int] = set()
        self.touched_spots: dict[int, str] = {}    # クラスターの再確認用（spot_id -> 理由）
        self._seen_updates: list = []              # osm_seen の更新（最後にまとめて書く）

    # ---------------------------------------------------------------- 記録
    def hist(self, spot_id, event, detail=None):
        # 履歴はためておき、最後にまとめて書く（本番DBは遠いので1件ずつ往復しない）
        self._hist.append((spot_id, self.run, event, json.dumps(detail or {}, ensure_ascii=False, default=str)))
        self.events[event] += 1

    def flush_history(self):
        if self._hist:
            with self.c.cursor() as cur:
                cur.executemany("insert into spot_history (spot_id, run_id, event, detail) values (%s,%s,%s,%s)",
                                self._hist)
            self._hist = []

    def next_id(self):
        """地点IDを500件ずつまとめて確保する（新規登録のたびに往復しない）"""
        if not self._ids:
            self._ids = [r["id"] for r in self.c.execute(
                "select nextval(pg_get_serial_sequence('spots','id')) id from generate_series(1, 500)").fetchall()]
        return self._ids.pop(0)

    def note(self, kind, s: Spot, text):
        if len(self.lists[kind]) < 40:
            self.lists[kind].append(f"{s.code} {s.name}：{text}")

    # ---------------------------------------------------------------- 読み込み
    def load(self):
        rows = self.c.execute("""
            select s.id, s.code, s.name, ST_X(s.geom::geometry) lon, ST_Y(s.geom::geometry) lat, s.category,
                   s.extra_categories, s.area_code, s.data_status, s.status, s.confidence, s.origin, s.locked,
                   s.missing_runs, s.archived_reason, s.sample_key, s.line_key, s.review_reason, s.osm_seen,
                   coalesce((select jsonb_object_agg(feature_key, role) from spot_features f where f.spot_id = s.id), '{}') feats
              from spots s""").fetchall()
        self.spots = {r["id"]: Spot(r["id"], r["code"], r["name"], (r["lon"], r["lat"]), r["category"],
                                    r["extra_categories"] or [], r["area_code"], r["data_status"], r["status"],
                                    r["confidence"], r["origin"], r["locked"], r["missing_runs"],
                                    r["archived_reason"], r["sample_key"], r["line_key"], r["review_reason"],
                                    dict(r["feats"]), r["osm_seen"]) for r in rows}
        self.by_feature = defaultdict(list)
        for s in self.spots.values():
            for k, role in s.features.items():
                self.by_feature[k].append(s.id)
        self.by_sample = {s.sample_key: s.id for s in self.spots.values() if s.sample_key}
        self.grid = Grid()
        for s in self.spots.values():
            self.grid.add(s.ll, s.id)
        self.features = {r["key"]: r for r in self.c.execute(
            "select key, kind, name, categories, missing_runs from features").fetchall()}
        self.codes = defaultdict(int)
        for s in self.spots.values():
            if s.code and s.code[-4:].isdigit():
                p = s.code.rsplit("-S", 1)[0]
                self.codes[p] = max(self.codes[p], int(s.code[-4:]))

    # ---------------------------------------------------------------- マスタ
    def masters(self):
        for code, name in LANDSCAPE_GROUPS.items():
            self.c.execute("""insert into landscape_groups (code, name) values (%s,%s)
                              on conflict (code) do update set name = excluded.name""", (code, name))
        for code, (name, grp, shape, spacing) in CATEGORIES.items():
            self.c.execute("""insert into spot_categories (code, name, landscape_group, shape, spacing_m, priority)
                              values (%s,%s,%s,%s,%s,%s)
                              on conflict (code) do update set name=excluded.name, landscape_group=excluded.landscape_group,
                                shape=excluded.shape, spacing_m=excluded.spacing_m, priority=excluded.priority""",
                           (code, name, grp, shape, spacing, PRIORITY[code]))
        for code, a in self.snap.areas.items():
            self.c.execute("""insert into areas (code, name, osm_relation, geom)
                              values (%s,%s,%s, ST_Multi(ST_GeomFromText(%s,4326))::geography)
                              on conflict (code) do update set name=excluded.name, osm_relation=excluded.osm_relation,
                                geom=excluded.geom, updated_at=now()""",
                           (code, a["name"], AREAS[code][1], a["wkt"]))

    # ---------------------------------------------------------------- 異常の検知
    def anomalies(self):
        """種別ごとに、基準（異常と判定されなかった直近の本番反映）の件数と比べる。
        一度「異常」と判定された種別は、人が確認して allow_large_change で受け入れるまで異常のまま"""
        runs = self.c.execute("""select counts, summary from data_runs where mode='apply' and status='ok'
                                   and counts is not null order by id desc limit 30""").fetchall()
        now = self.snap.meta["by_category"]
        bad, rows = set(), []
        for cat in CATEGORIES:
            ref = None
            for r in runs:
                frozen = set((r["summary"] or {}).get("frozen_categories") or [])
                if cat not in frozen:
                    ref = (r["counts"] or {}).get(cat)
                    break
            n = now.get(cat, 0)
            flag = ""
            if ref and ref >= 10 and n < ref * (1 - DROP_ANOMALY) and not self.allow_large_change:
                bad.add(cat); flag = "！異常な減少（この種別は消失扱いしない）"
            rows.append((cat, ref, n, flag))
        for cat in self.snap.meta.get("incomplete_categories") or []:      # 元データの取得が不完全（標高タイルなど）
            bad.add(cat)
            rows = [(c, r, n, "！元データの取得が不完全（この種別は消失扱いしない）" if c == cat else f)
                    for c, r, n, f in rows]
        return bad, rows, bool(runs)

    # ---------------------------------------------------------------- 地点の更新
    def new_code(self, area_code):
        p = "FUK-" + area_code.split("-")[1][:3].upper()
        self.codes[p] += 1
        return f"{p}-S{self.codes[p]:04d}"

    def create(self, name, ll, cat, extra, area, kind, feature_key, sample=None):
        if not name:
            name = f"（無名）{CATEGORIES[cat][0]}"
        code = self.new_code(area)
        sid = self.next_id()
        self.c.execute(
            """insert into spots (id, code, name, area_code, geom, category, extra_categories, position_kind,
                                  line_geom, line_key, sample_key, created_run, last_seen_run)
               values (%s,%s,%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, %s,%s,%s,
                       case when %s::float8 is null then null else ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography end,
                       %s,%s,%s,%s)""",
            (sid, code, name, area, ll[0], ll[1], cat, extra, kind,
             sample.line_lon if sample else None, sample.line_lon if sample else None, sample.line_lat if sample else None,
             sample.line_key if sample else None, sample.key if sample else None, self.run, self.run))
        s = Spot(sid, code, name, ll, cat, extra, area, "current", "candidate", "unverified", "osm", False, 0,
                 None, sample.key if sample else None, sample.line_key if sample else None, None, {})
        self.spots[sid] = s
        self.grid.add(ll, sid)
        self.link(s, feature_key, "primary")
        self.hist(sid, "created", {"feature": feature_key, "category": cat, "lon": ll[0], "lat": ll[1]})
        self.seen_spots.add(sid)
        return s

    def link(self, s: Spot, key, role):
        if key in s.features:
            return
        self.c.execute("""insert into spot_features (spot_id, feature_key, role, linked_run) values (%s,%s,%s,%s)
                          on conflict do nothing""", (s.id, key, role, self.run))
        s.features[key] = role
        self.by_feature[key].append(s.id)

    def flag_review(self, s: Spot, reason, detail, force=False):
        if s.data_status == "archived":
            return
        if s.data_status == "needs_review" and s.review_reason == reason and not force:
            return
        self.c.execute("update spots set data_status='needs_review', review_reason=%s where id=%s", (reason, s.id))
        s.data_status, s.review_reason = "needs_review", reason
        self.hist(s.id, "needs_review", {"reason": reason, **detail})
        self.note("要確認", s, reason)
        self.touched_spots[s.id] = reason

    def restore_if_archived(self, s: Spot):
        if s.data_status == "archived" and (s.archived_reason or "").startswith("auto:"):
            self.c.execute("""update spots set data_status='current', archived_reason=null, archived_at=null,
                              archived_run=null, missing_runs=0 where id=%s""", (s.id,))
            self.hist(s.id, "restored", {"was": s.archived_reason})
            self.note("復帰", s, "OSMに戻ったため対象外を解除")
            s.data_status, s.archived_reason = "current", None

    def seen(self, s: Spot, state: dict | None):
        """保護された地点について、今回見たOSMの値を記録する（変わったときだけ書く。None で消す）"""
        if state != s.osm_seen:
            s.osm_seen = state
            self._seen_updates.append((None if state is None else json.dumps(state, ensure_ascii=False), s.id))

    def update(self, s: Spot, name, ll, cat, extra, area):
        """自動で更新してよい地点は更新、人が確認した地点は要確認にする"""
        if area and area != s.area_code:
            self.c.execute("update spots set area_code=%s where id=%s", (area, s.id))
            self.hist(s.id, "area", {"from": s.area_code, "to": area})
            s.area_code = area
        if s.protected:
            # 地点の今の値ではなく「前回見たOSMの値」と比べる（OSMが変わったときだけ要確認）
            b = s.osm_seen if s.osm_seen and "lon" in s.osm_seen else \
                {"name": s.name, "lon": s.ll[0], "lat": s.ll[1], "cats": [s.category] + sorted(s.extra)}
            changes = {}
            moved = dist_m((b["lon"], b["lat"]), ll)
            if moved > MOVE_M:
                changes["moved"] = {"from": (b["lon"], b["lat"]), "to": ll, "m": round(moved)}
            if name and name != b.get("name"):
                changes["renamed"] = {"from": b.get("name"), "to": name}
            if [cat] + sorted(extra) != b["cats"]:
                changes["recategorized"] = {"from": b["cats"], "to": [cat] + extra}
            self.seen(s, {"name": name or None, "lon": round(ll[0], 7), "lat": round(ll[1], 7),
                          "cats": [cat] + sorted(extra)} if changes or s.osm_seen else s.osm_seen)
            if changes:
                self.flag_review(s, "OSMの位置・名前・種別が変わった", {"proposed": changes}, force=True)
            return
        if s.osm_seen is not None:
            # 保護が外れた（固定を外した等）：以後はOSMに合わせるので比較の基準は残さない（再び固定したときに古い基準を使わない）
            self.seen(s, None)
        changes = {}
        moved = dist_m(s.ll, ll)
        if moved > MOVE_M:
            changes["moved"] = {"from": s.ll, "to": ll, "m": round(moved)}
        if name and name != s.name:                         # 名前が消えたときは旧名を残す
            changes["renamed"] = {"from": s.name, "to": name}
        if cat != s.category or sorted(extra) != sorted(s.extra):
            changes["recategorized"] = {"from": [s.category] + s.extra, "to": [cat] + extra}
        if not changes:
            return
        if "moved" in changes:
            self.c.execute("update spots set geom=ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography where id=%s",
                           (ll[0], ll[1], s.id))
            if changes["moved"]["m"] >= CLUSTER_MOVE_M:
                self.touched_spots[s.id] = f"地点が{changes['moved']['m']}m移動"
            s.ll = ll
            self.hist(s.id, "moved", changes["moved"])
        if "renamed" in changes:
            self.c.execute("update spots set name=%s, alt_names = array_append(alt_names, %s) where id=%s",
                           (name, s.name, s.id))
            self.hist(s.id, "renamed", changes["renamed"]); self.note("名前の変更", s, f"→ {name}")
            s.name = name
        if "recategorized" in changes:
            self.c.execute("update spots set category=%s, extra_categories=%s where id=%s", (cat, extra, s.id))
            self.hist(s.id, "recategorized", changes["recategorized"])
            if group_of(cat) != group_of(s.category):
                self.touched_spots[s.id] = "景観が変わった"
            s.category, s.extra = cat, extra
        if s.status == "ai_tagged" and ("renamed" in changes or "recategorized" in changes):
            # 名前・種別が変わったAI分類は古くなるので、次のAI分類でやり直す
            self.c.execute("update spots set status='candidate', confidence='unverified' where id=%s", (s.id,))
            self.hist(s.id, "reclassify", {"reason": "名前・種別の変化"})
            s.status, s.confidence = "candidate", "unverified"

    # ---------------------------------------------------------------- 点・面
    def sync_points(self, missing_now: set[str]):
        with self.c.cursor() as cur:
            cur.executemany(
                """insert into features (key, kind, osm_type, osm_id, name, categories, tags, geom, extent_m,
                                         first_seen_run, last_seen_run, missing_runs, updated_at)
                   values (%s,'element',%s,%s,%s,%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography,%s,%s,%s,0,now())
                   on conflict (key) do update set name=excluded.name, categories=excluded.categories, tags=excluded.tags,
                     geom=excluded.geom, extent_m=excluded.extent_m, last_seen_run=excluded.last_seen_run,
                     missing_runs=0, updated_at=now()""",
                [(f.key, f.osm_type, f.osm_id, f.name or None, f.cats, json.dumps(f.tags, ensure_ascii=False),
                  f.lon, f.lat, f.extent_m, self.run, self.run) for f in self.snap.points])
        for f in self.snap.points:
            cat, extra = f.cats[0], f.cats[1:]
            ll = (f.lon, f.lat)
            linked = [self.spots[i] for i in self.by_feature.get(f.key, [])]
            if linked:
                for s in linked:
                    self.seen_spots.add(s.id)
                    self.restore_if_archived(s)
                    if s.features.get(f.key) == "primary" and s.data_status != "archived":
                        self.update(s, f.name, ll, cat, extra, f.area_code)
                continue
            nn, grp = norm_name(f.name), group_of(cat)
            # 1) 同じ場所の別の要素（寺の「点」と「敷地」、上下線で分かれた橋など）→ 同じ地点にまとめる
            best = None
            for d, sid in self.grid.near(ll, max(80.0, f.extent_m)):
                s = self.spots[sid]
                if s.sample_key or s.data_status == "archived":
                    continue
                same_name = nn and norm_name(s.name) == nn and group_of(s.category) == grp
                same_unnamed = not nn and s.name.startswith("（無名）") and s.category == cat and d <= 30
                if (same_name or same_unnamed) and (best is None or d < best[0]):
                    best = (d, s)
            if best and not all(k in missing_now for k in best[1].features):
                self.link(best[1], f.key, "secondary")
                self.seen_spots.add(best[1].id)
                self.hist(best[1].id, "linked", {"feature": f.key, "m": round(best[0])})
                continue
            # 2) OSM の ID が変わった（消えた要素と同じ名前・近い位置）→ 付け替え
            if best or nn:
                cand = best
                if not cand:
                    for d, sid in self.grid.near(ll, max(150.0, f.extent_m)):
                        s = self.spots[sid]
                        if not s.sample_key and norm_name(s.name) == nn and group_of(s.category) == grp \
                                and s.features and all(k in missing_now for k in s.features) \
                                and (cand is None or d < cand[0]):
                            cand = (d, s)
                if cand:
                    s = cand[1]
                    old = [k for k, r in s.features.items() if r == "primary"]
                    self.c.execute("update spot_features set role='secondary' where spot_id=%s and role='primary'", (s.id,))
                    for k in old:
                        s.features[k] = "secondary"
                    self.link(s, f.key, "primary")
                    self.c.execute("update spot_features set role='primary' where spot_id=%s and feature_key=%s",
                                   (s.id, f.key))
                    s.features[f.key] = "primary"
                    self.hist(s.id, "relinked", {"from": old, "to": f.key})
                    self.note("付け替え", s, f"OSMの要素が {','.join(old)} → {f.key}")
                    self.seen_spots.add(s.id)
                    self.restore_if_archived(s)
                    self.update(s, f.name, ll, cat, extra, f.area_code)
                    continue
            self.create(f.name, ll, cat, extra, f.area_code, "area" if f.extent_m > 0 and f.osm_type != "n" else "point",
                        f.key)

    # ---------------------------------------------------------------- 線
    def sync_lines(self, missing_samples_ok: bool):
        lines = defaultdict(lambda: {"ways": set(), "name": "", "cat": ""})
        for smp in self.snap.samples:
            L = lines[smp.line_key]
            L["ways"].update(smp.way_ids); L["name"], L["cat"] = smp.name, smp.cat
        with self.c.cursor() as cur:
            cur.executemany(
                """insert into features (key, kind, name, categories, way_ids, first_seen_run, last_seen_run, missing_runs, updated_at)
                   values (%s,'line',%s,%s,%s,%s,%s,0,now())
                   on conflict (key) do update set name=excluded.name, way_ids=excluded.way_ids,
                     last_seen_run=excluded.last_seen_run, missing_runs=0, updated_at=now()""",
                [(key, L["name"] or None, [L["cat"]], sorted(L["ways"]), self.run, self.run) for key, L in lines.items()])
        seen_keys = {smp.key for smp in self.snap.samples}
        for smp in self.snap.samples:
            ll = (smp.lon, smp.lat)
            sid = self.by_sample.get(smp.key)
            if sid:
                s = self.spots[sid]
                self.seen_spots.add(sid)
                self.restore_if_archived(s)
                if s.data_status != "archived":
                    self.update(s, smp.name, ll, smp.cat, [], smp.area_code)
                    self.c.execute("""update spots set position_kind=%s,
                                        line_geom=ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography
                                      where id=%s and not (position_kind = 'manual' and locked)""",
                                   (smp.position_kind, smp.line_lon, smp.line_lat, sid))
                continue
            # 名前の変更・格子のずれ：近くの「今回見つからなかった」同じ種別の点を付け替え
            cand = None
            r = CATEGORIES[smp.cat][3] * 0.5
            for d, sid2 in self.grid.near(ll, r):
                s = self.spots[sid2]
                if s.sample_key and s.sample_key not in seen_keys and s.category == smp.cat \
                        and s.data_status != "archived" and (cand is None or d < cand[0]):
                    cand = (d, s)
            if cand:
                s = cand[1]
                old = s.sample_key
                self.c.execute("update spots set sample_key=%s, line_key=%s where id=%s", (smp.key, smp.line_key, s.id))
                del self.by_sample[old]
                self.by_sample[smp.key] = s.id
                s.sample_key, s.line_key = smp.key, smp.line_key
                self.link(s, smp.line_key, "primary")
                self.hist(s.id, "relinked", {"from": old, "to": smp.key})
                self.seen_spots.add(s.id)
                self.update(s, smp.name, ll, smp.cat, [], smp.area_code)
                continue
            s = self.create(smp.name, ll, smp.cat, [], smp.area_code, smp.position_kind, smp.line_key, smp)
            self.by_sample[smp.key] = s.id
        # 坂道の測定値（長さ・勾配・高低差・上りの方角）。人が直した地点でも測定値は最新にする
        attrs = [(a["slope_len_m"], a["slope_grade_pct"], a["slope_rise_m"], a["slope_bearing"], a["slope_kind"],
                  self.by_sample[smp.key])
                 for smp in self.snap.samples if (a := smp.attrs) and smp.key in self.by_sample]
        if attrs:
            with self.c.cursor() as cur:
                cur.executemany("""update spots set slope_len_m=%s, slope_grade_pct=%s, slope_rise_m=%s,
                                          slope_bearing=%s, slope_kind=%s where id=%s""", attrs)

    # ---------------------------------------------------------------- 見つからなかった地点
    def missing(self, present_keys: dict, frozen: set[str], rules_changed: bool):
        for s in self.spots.values():
            if s.id in self.seen_spots or s.origin != "osm" or s.data_status == "archived":
                continue
            cats = {s.category, *s.extra}
            if cats & frozen:
                continue                      # 異常に減った種別：今回は判断しない
            if s.sample_key:
                why, certain = "線の形が変わり、この位置に線がなくなった", False
            else:
                keys = list(s.features)
                still = [k for k in keys if k in present_keys]
                if still:
                    why = "OSMには残っているが収集条件から外れた" + ("（収集条件を変更）" if rules_changed else "（タグの変更）")
                    certain = True
                else:
                    why, certain = "OSMから削除された", False
            if s.protected:
                if (s.osm_seen or {}).get("missing") != why:      # 同じ理由は一度だけ（解消後にぶり返さない）
                    # 前回見た名前・位置は残す（戻ってきたとき、人が直した値ではなく前回のOSMの値と比べる）
                    self.seen(s, {**{k: v for k, v in (s.osm_seen or {}).items() if k != "missing"}, "missing": why})
                    self.flag_review(s, why, {"missing": True})
                continue
            runs = s.missing_runs + 1
            self.c.execute("update spots set missing_runs=%s where id=%s", (runs, s.id))
            self.hist(s.id, "missing", {"reason": why, "runs": runs})
            if certain or runs >= MISSING_ARCHIVE_RUNS:
                self.c.execute("""update spots set data_status='archived', archived_reason=%s, archived_at=now(),
                                  archived_run=%s where id=%s""", ("auto:" + why, self.run, s.id))
                self.hist(s.id, "archived", {"reason": why})
                self.note("対象外", s, why)
                self.touched_spots[s.id] = "地点が対象外になった"
                s.data_status = "archived"
        # 見つかった地点は連続未検出をリセット
        if self.seen_spots:
            self.c.execute("update spots set missing_runs=0, last_seen_run=%s where id = any(%s)",
                           (self.run, list(self.seen_spots)))
        self.c.execute("update features set missing_runs = missing_runs + 1 where last_seen_run is distinct from %s",
                       (self.run,))

    def clusters(self):
        if not self.touched_spots:
            return 0
        rows = self.c.execute(
            """update clusters c set needs_review = true,
                      review_note = coalesce(review_note || E'\n', '') || to_char(now(),'YYYY-MM-DD') || ' 地点の変化：' || x.why
                 from (select cs.cluster_id, string_agg(distinct s.name || '（' || t.why || '）', '、') why
                         from cluster_spots cs join spots s on s.id = cs.spot_id
                         join unnest(%s::bigint[], %s::text[]) t(id, why) on t.id = cs.spot_id
                        group by cs.cluster_id) x
                where c.id = x.cluster_id and c.origin <> 'auto' and c.status <> 'archived'
                returning c.id""",
            (list(self.touched_spots), list(self.touched_spots.values()))).fetchall()
        return len(rows)

    # ---------------------------------------------------------------- 実行
    def run_all(self, present_keys: dict) -> dict:
        self.masters()
        self.load()
        frozen, count_rows, has_prev = self.anomalies()
        prev_rules = self.c.execute("""select rules_version from data_runs where mode='apply' and status='ok'
                                       order by id desc limit 1""").fetchone()
        rules_changed = bool(prev_rules and prev_rules["rules_version"] != RULES_VERSION)
        snap_keys = {f.key for f in self.snap.points}
        missing_now = {k for k, f in self.features.items() if f["kind"] == "element" and k not in snap_keys}
        n_before = sum(1 for s in self.spots.values() if s.data_status != "archived")
        with self.c.pipeline():                 # 書き込みをまとめて送る（往復を減らす）
            self.sync_points(missing_now)
            self.sync_lines(True)
            self.missing(present_keys, frozen, rules_changed)
            if self._seen_updates:
                with self.c.cursor() as cur:
                    cur.executemany("update spots set osm_seen=%s::jsonb where id=%s", self._seen_updates)
            self.flush_history()
        clusters = self.clusters()
        n_after = self.c.execute("select count(*) n from spots where data_status <> 'archived'").fetchone()["n"]
        summary = {
            "spots_before": n_before, "spots_after": n_after,
            "created": self.events["created"], "moved": self.events["moved"], "renamed": self.events["renamed"],
            "recategorized": self.events["recategorized"], "linked": self.events["linked"],
            "relinked": self.events["relinked"], "needs_review": self.events["needs_review"],
            "missing": self.events["missing"], "archived": self.events["archived"],
            "restored": self.events["restored"], "area_changed": self.events["area"],
            "clusters_to_review": clusters, "frozen_categories": sorted(frozen), "rules_changed": rules_changed,
        }
        self.count_rows = count_rows
        return summary

    def report(self, summary: dict) -> str:
        L = ["# 収集の差分レポート", ""]
        L.append(f"- 地点（対象外を除く）: {summary['spots_before']} → {summary['spots_after']}")
        for k, label in [("created", "新規"), ("moved", "位置の更新"), ("renamed", "名前の更新"),
                         ("recategorized", "種別の更新"), ("linked", "同じ場所の要素をまとめた"),
                         ("relinked", "OSMの要素の付け替え（ID変更など）"), ("needs_review", "要確認にした"),
                         ("missing", "見つからなかった"), ("archived", "対象外にした"), ("restored", "対象外から戻した"),
                         ("area_changed", "区の更新"), ("clusters_to_review", "再確認が必要な保存済みの流れ")]:
            L.append(f"- {label}: {summary[k]}")
        if summary["frozen_categories"]:
            L.append(f"- ！異常に減ったため消失扱いを止めた種別: {', '.join(summary['frozen_categories'])}")
        if summary["rules_changed"]:
            L.append("- 収集条件が前回から変わっています（条件から外れた地点は対象外になります）")
        L += ["", "## 種別ごとの件数（前回 → 今回）"]
        for cat, p, n, flag in self.count_rows:
            L.append(f"- {CATEGORIES[cat][0]}: {p if p is not None else '—'} → {n} {flag}")
        for kind, items in self.lists.items():
            L += ["", f"## {kind}（最大40件）"] + [f"- {x}" for x in items]
        return "\n".join(L)
