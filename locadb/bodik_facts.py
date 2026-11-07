"""BODIK（福岡市オープンデータ）の情報を、既存の地点の「事実カード」（spot_facts）として足す（AIなし・費用ゼロ・DB変更なし）

  python -m locadb.bodik_facts --dry-run [--only inn,park,waterspot,eatery] [--out DIR]   読み取りのみ。事実の一覧（JSON・CSV）を出し、取り込みを試して取り消す
  python -m locadb.bodik_facts --apply   [--only ...]                                      spot_facts に取り込む（承認者は --approved-by。ログインなしの画面にもすぐ出る）

足す事実（利用者と合意 2026-11-06）
  inn        旅館業営業許可施設一覧   → 規模「客室○室・定員○人」
  park       都市公園（Shape）        → 規模「供用面積」
  waterspot  給水スポット             → 管理者・所有者（データの団体名）
  eatery     飲食店営業許可施設一覧   → 創設・開設年（営業許可の初年度の許可日。開業年とは限らないと値の脇に書く）
  足さないもの：旅館業・公衆浴場の「許可年月日」（旅館業は約8割が令和の日付＝更新・代替わりの日で、開設年にならない）、
  公共施設・Wi-Fi の団体名・設置者（BODIK 側が全件空）、氏名の列（読みもしない）。

結びつけ：名前が一致（表記ゆれは整える）し、地点が近い（既定150m。公園は面積に応じて広げる）ものだけ。
  同じ名前の地点が近くに複数ある・同じ地点に別の値が複数ある・その地点にすでに同じ項目の承認済みの事実がある、ものは足さず一覧に残す。
  住所だけの一覧は、名前が既存の地点と一致したものだけ国土地理院の住所検索で座標を出す。番地まで引けなければ足さない。
出典：source_kind='web'、出典URL＝データセットのページ、記事名＝「BODIK ODCS 福岡市「…」」、ライセンス＝CC BY 4.0（地点ページに出典とライセンスが出る）。
"""
import argparse
import csv
import json
import math
import re
import time
import unicodedata
from datetime import date
from pathlib import Path

import requests

from . import bodik_import as bi
from .db import connect, job

NEAR_M = 150
PARK_EXTRA_MAX_M = 1000
PARK_SCATTER_MAX_M = 300                 # 同じ名前の公園の区画がこれ以上離れていたら、面積を合算した値は使わない
FOOD_CATEGORIES = ("restaurant", "cafe", "bar")   # 飲食店営業許可の事実を足してよい地点の分類
APPROVED_BY = "hirata"
TARGETS = ("inn", "park", "waterspot", "eatery")
ITEMS = ("管理者・所有者", "創設・開設年", "規模", "現況", "指定・登録", "人口")
BATCH = 200


# ------------------------------------------------------------------ 名前・距離
def norm(name: str) -> str:
    """名前の比較用。全角半角・空白・括弧の補足・「福岡市立」「株式会社」などを整える"""
    s = unicodedata.normalize("NFKC", name or "").lower()
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    s = re.sub(r"株式会社|有限会社|合同会社|\(株\)|\(有\)", "", s)
    s = re.sub(r"^(福岡市立|福岡県立|福岡市)", "", s)
    return re.sub(r"[\s・･\-‐−ー—]+", "", s)


def meters(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742000 * math.asin(math.sqrt(a))


class SpotIndex:
    """地点を名前（整えたもの）で引く。alt_names も使う"""
    def __init__(self, spots: list[dict]):
        self.by_name: dict[str, list[dict]] = {}
        for s in spots:
            for n in {norm(s["name"]), *(norm(a) for a in (s.get("alt_names") or []))}:
                if n:
                    self.by_name.setdefault(n, []).append(s)

    def has(self, name: str) -> bool:
        return norm(name) in self.by_name

    def near(self, name: str, lat: float, lon: float, radius_m: float) -> list[dict]:
        out = []
        for s in self.by_name.get(norm(name), []):
            d = meters(lat, lon, s["lat"], s["lon"])
            if d <= radius_m:
                out.append({**s, "dist_m": round(d)})
        return out


# ------------------------------------------------------------------ 事実の形
def source_of(spec_key: str) -> dict:
    spec = bi.SPEC_BY_KEY[spec_key]
    return {"source_kind": "web", "source_url": bi.DATASET_PAGE + spec["pkg"],
            "source_title": f"BODIK ODCS 福岡市「{spec['label']}」", "license": bi.LICENSE}


def positive_int(v):
    m = re.fullmatch(r"\d+", bi.clean(v))
    return int(m.group()) if m and int(m.group()) > 0 else None


def scale_text(area_m2: float) -> str:
    """供用面積の値。1ヘクタール未満は㎡、以上はヘクタール（既存の事実の書き方にそろえる）"""
    if area_m2 < 10000:
        return f"{round(area_m2):,}m2"
    ha = f"{area_m2 / 10000:.2f}".rstrip("0").rstrip(".")
    return f"{ha}ヘクタール"


def first_permit_year(v: str):
    m = re.match(r"(\d{4})/\d{1,2}/\d{1,2}", bi.clean(v))
    return int(m[1]) if m and 1900 <= int(m[1]) <= date.today().year else None


def as_of_text(today: date) -> str:
    return f"{today.year}年{today.month}月取得（福岡市オープンデータ）"


# ------------------------------------------------------------------ 一覧 → 事実の材料
# 材料：{"dataset","name","item","value","note","as_of","quote","lat","lon","addr","radius"}。lat/lon がなければ addr から引く
def inn_claims(records: list[dict], today: date) -> list[dict]:
    spec = bi.SPEC_BY_KEY["inn"]
    out = []
    for r in records:
        name = bi.display_name(bi.first(r, spec["name"]), "inn")
        addr = bi.first(r, spec["addr"])
        rooms, cap = positive_int(r.get("客室数")), positive_int(r.get("定員数"))
        if not name or not addr or not (rooms or cap):
            continue
        value = "・".join(p for p in (f"客室{rooms}室" if rooms else "", f"定員{cap}人" if cap else "") if p)
        kind = bi.clean(r.get("旅館業の種別"))
        out.append({"dataset": "inn", "name": name, "addr": addr, "lat": None, "lon": None, "radius": NEAR_M,
                    "item": "規模", "value": value, "note": "旅館業営業許可の届出内容" + (f"（{kind}）" if kind else ""),
                    "as_of": as_of_text(today), "quote": f"{name}／{addr}／客室数{rooms or '-'}／定員数{cap or '-'}／{kind}"})
    return out


def park_claims(items: list[dict], stats: dict | None = None) -> list[dict]:
    """bodik_import.items_from_shape の結果（名前・代表点・面積）から。広い公園は地点の置き場所がずれるので探す範囲を広げる"""
    out = []
    scattered = 0
    for it in items:
        area = float(it["rank"] or 0)
        if area <= 0:
            continue
        if float(it.get("gap_m") or 0) > PARK_SCATTER_MAX_M:                          # 離れた同名の公園を合算している
            scattered += 1
            continue
        kind = it["detail"].get("公園種別") or ""
        out.append({"dataset": "park", "name": it["name"], "addr": None, "lat": it["lat"], "lon": it["lon"],
                    "radius": min(NEAR_M + math.sqrt(area), PARK_EXTRA_MAX_M),
                    "item": "規模", "value": scale_text(area), "note": "供用面積" + (f"（{kind}）" if kind else ""),
                    "as_of": None, "quote": f"{it['name']}／{it['detail'].get('区名', '')}／供用面積{round(area)}m2"})
    if stats is not None:
        stats["park_scattered_skipped"] = scattered
    return out


def waterspot_claims(records: list[dict]) -> list[dict]:
    spec = bi.SPEC_BY_KEY["waterspot"]
    out = []
    for r in records:
        name = bi.display_name(bi.first(r, spec["name"]), "waterspot")
        lat, lon = bi.to_float(bi.first(r, spec["lat"])), bi.to_float(bi.first(r, spec["lon"]))
        org = bi.clean(r.get("団体名"))
        if not name or not bi.in_bbox(lat, lon) or not org:
            continue
        out.append({"dataset": "waterspot", "name": name, "addr": None, "lat": lat, "lon": lon, "radius": NEAR_M,
                    "item": "管理者・所有者", "value": org, "note": "給水スポットの設置団体", "as_of": None,
                    "quote": f"{name}／団体名{org}"})
    return out


def eatery_claims(records: list[dict], today: date) -> list[dict]:
    spec = bi.SPEC_BY_KEY["eatery"]
    out = []
    for r in records:
        m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", bi.clean(r.get(spec["expire_col"])))
        if m and date(int(m[1]), int(m[2]), int(m[3])) < today:                      # 許可の期限が切れた店は足さない
            continue
        name = bi.display_name(bi.first(r, spec["name"]), "eatery")
        addr = bi.first(r, spec["addr"])
        year = first_permit_year(r.get("初年度許可・交付日"))
        if not name or not addr or not year:
            continue
        out.append({"dataset": "eatery", "name": name, "addr": addr, "lat": None, "lon": None, "radius": NEAR_M,
                    "categories": FOOD_CATEGORIES, "item": "創設・開設年", "value": f"{year}年（営業許可の初年度）",
                    "note": "飲食店営業許可の初年度の許可日。営業者の変更などで新しくなることがあり、開業年とは限らない",
                    "as_of": None, "quote": f"{name}／{addr}／初年度許可{bi.clean(r.get('初年度許可・交付日'))}"})
    return out


# ------------------------------------------------------------------ 地点に結びつける
def match_claims(claims: list[dict], index: SpotIndex, have: set, stats: dict, session=requests, sleep=time.sleep,
                 limit: int = 0) -> tuple[list[dict], list[dict]]:
    """材料 → 事実（facts_import の形）と、足さなかったものの一覧。have：すでに承認済みの (spot_id, item)"""
    facts, left = [], []
    by_spot: dict[tuple, list[dict]] = {}
    geocoded = 0
    for c in claims:
        ds = c["dataset"]
        s = stats.setdefault(ds, {})
        s["claims"] = s.get("claims", 0) + 1
        if not index.has(c["name"]):
            s["no_spot_name"] = s.get("no_spot_name", 0) + 1
            continue
        lat, lon = c["lat"], c["lon"]
        if lat is None:
            if limit and geocoded >= limit:
                s["geocode_skipped_limit"] = s.get("geocode_skipped_limit", 0) + 1
                continue
            geocoded += 1
            pt = bi.geocode(c["addr"], session, sleep)
            if pt is None:
                s["geocode_failed"] = s.get("geocode_failed", 0) + 1
                continue
            lat, lon = pt
        near = index.near(c["name"], lat, lon, c["radius"])
        if not near:
            s["name_but_far"] = s.get("name_but_far", 0) + 1
            continue
        if c.get("categories"):
            near = [x for x in near if x.get("category") in c["categories"]]
            if not near:
                s["wrong_category"] = s.get("wrong_category", 0) + 1       # 同名・近くでも、病院や学校などの地点には足さない
                continue
        if len(near) > 1:
            s["ambiguous_spots"] = s.get("ambiguous_spots", 0) + 1
            left.append({**c, "why": "同じ名前の地点が近くに複数ある", "spots": near})
            continue
        spot = near[0]
        by_spot.setdefault((spot["id"], c["item"]), []).append({**c, "spot": spot})
    for (spot_id, item), cs in by_spot.items():
        ds = cs[0]["dataset"]
        s = stats[ds]
        if (spot_id, item) in have:
            s["spot_has_fact"] = s.get("spot_has_fact", 0) + 1
            continue
        if len({c["value"] for c in cs}) > 1:
            s["conflicting_values"] = s.get("conflicting_values", 0) + 1
            left.extend({**c, "why": "同じ地点に別の値がある", "spots": [c["spot"]]} for c in cs)
            continue
        c = cs[0]
        facts.append({"spot_id": spot_id, "item": item, "value": c["value"], "note": c["note"], "as_of": c["as_of"],
                      **source_of(ds), "quote": c["quote"], "_csv": [ds, c["name"], c["spot"]["code"], c["spot"]["name"], c["spot"]["dist_m"]]})
        s["facts"] = s.get("facts", 0) + 1
    return facts, left


# ------------------------------------------------------------------ 取得
def fetch_records(key: str, session=requests, sleep=time.sleep) -> list[dict]:
    spec = bi.SPEC_BY_KEY[key]
    res = [r for r in bi.package_resources(spec["pkg"], session, sleep) if r.get("url") and (r.get("format") or "").upper() in (spec.get("formats") or ["CSV"])]
    records = []
    for r in res:
        got = bi.http_get(r["url"], None, session, sleep)
        if got is not None:
            records += bi.read_table(bi.decode(got.content))
    return records


def collect_claims(conn, key: str, stats: dict, workdir: Path, today: date, session=requests, sleep=time.sleep) -> list[dict]:
    if key == "park":
        st: dict = {}
        items = bi.collect(conn, bi.SPEC_BY_KEY["park"], st, workdir, session, sleep)
        stats.setdefault("park", {}).update({k: v for k, v in st.items() if k in ("no_shape", "fetch_failed", "park_items", "skipped_null_shape")})
        return park_claims(items, stats.setdefault("park", {}))
    records = fetch_records(key, session, sleep)
    stats.setdefault(key, {})["records"] = len(records)
    if key == "inn":
        return inn_claims(records, today)
    if key == "waterspot":
        return waterspot_claims(records)
    return eatery_claims(records, today)


SPOTS_SQL = """
select id, code, name, alt_names, category, ST_Y(geom::geometry) as lat, ST_X(geom::geometry) as lon
  from spots where data_status <> 'archived'
"""


def run_import(conn, facts: list[dict], user: str, apply: bool) -> dict:
    """facts_import（DB関数）に渡す。弾かれた行が1件でもあれば、全部取り消す。dry-run も取り込んでから取り消す"""
    import psycopg
    total = {"added": 0, "skipped": 0, "rejected": []}
    payload = [{k: v for k, v in f.items() if not k.startswith("_")} for f in facts]
    with conn.transaction():
        for i in range(0, len(payload), BATCH):
            r = conn.execute("select facts_import(%s::jsonb, %s) as r", (json.dumps(payload[i:i + BATCH], ensure_ascii=False), user)).fetchone()["r"]
            total["added"] += r["added"]
            total["skipped"] += r["skipped"]
            total["rejected"] += r["rejected"]
        if not (apply and not total["rejected"]):
            raise psycopg.Rollback()
    total["committed"] = bool(apply and not total["rejected"])
    return total


def main(argv=None):
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default="", help="カンマ区切り（" + "、".join(TARGETS) + "）。空なら全部")
    ap.add_argument("--limit", type=int, default=0, help="住所だけの一覧で、座標を引く件数の上限（0＝制限なし）")
    ap.add_argument("--approved-by", default=APPROVED_BY)
    ap.add_argument("--out", default="bodik-facts-out")
    a = ap.parse_args(argv)
    keys = [k.strip() for k in a.only.split(",") if k.strip()] or list(TARGETS)
    bad = [k for k in keys if k not in TARGETS]
    if bad:
        raise SystemExit(f"知らないデータセット：{bad}")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    today = date.today()
    with connect() as conn, job(conn, "bodik_facts", None) as stats:
        stats.update(dry_run=a.dry_run, datasets=keys)
        index = SpotIndex(conn.execute(SPOTS_SQL).fetchall())
        have = {(r["spot_id"], r["item"]) for r in conn.execute("select spot_id, item from spot_facts where status = 'approved'").fetchall()}
        facts, left = [], []
        for k in keys:
            claims = collect_claims(conn, k, stats, out, today)
            f, l = match_claims(claims, index, have, stats, limit=a.limit if k in ("inn", "eatery") else 0)
            facts += f
            left += l
        res = run_import(conn, facts, a.approved_by, a.apply)
        stats["import"] = {**res, "rejected_count": len(res["rejected"]), "rejected": res["rejected"][:10]}
        doc = {"approved_by": a.approved_by, "items": [{k: v for k, v in f.items() if not k.startswith("_")} for f in facts]}
        (out / "bodik-facts.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        with open(out / "bodik-facts.csv", "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["データセット", "BODIKの名前", "地点コード", "地点名", "距離m", "項目", "値", "細目", "時点"])
            for f in facts:
                w.writerow([*f["_csv"], f["item"], f["value"], f["note"], f["as_of"] or ""])
        with open(out / "bodik-facts-left.csv", "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["データセット", "BODIKの名前", "理由", "項目", "値", "地点（コード・名前・距離m）"])
            for c in left:
                w.writerow([c["dataset"], c["name"], c["why"], c["item"], c["value"],
                            " / ".join(f"{s['code']}・{s['name']}・{s['dist_m']}" for s in c["spots"])])
        print(json.dumps({**stats}, ensure_ascii=False))
    if res["rejected"]:                                     # 記録と成果物を残してから止める
        raise SystemExit("弾かれた行があるため、取り込まずに止めました")


if __name__ == "__main__":
    main()
