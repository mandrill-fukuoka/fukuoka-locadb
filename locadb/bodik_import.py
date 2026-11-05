"""追加候補にBODIK（福岡市オープンデータ）を加える（AIなし・費用ゼロ）

  python -m locadb.bodik_import --dry-run [--only 公園,図書館] [--out DIR]    読み取りのみ。候補の一覧（CSV）を出すだけ
  python -m locadb.bodik_import --apply   [--only ...]                       候補を spot_additions に保存する（公開はしない）

データ元：BODIK ODCS（https://data.bodik.jp/）の福岡市（組織ID 401307）。ライセンスは各データセットとも CC BY 4.0。
  - 取得は CKAN API（package_show）→ CSV / Shape。1秒以上の間隔で直列。User-Agent に連絡先。
  - 追加候補は addition_candidates と同じ決まり：福岡市7区の中／近く150m以内に既存の地点がある・近く1km以内に同じ名前がある場合は出さない。
  - 採用は人だけ（確認画面の「追加候補」→ addition_decide）。撮影の可否・評価は書かない。種別の初期値は一覧の種別や名前の語から決めた案。
  - 取り込まないもの：営業者・届出者などの氏名の列（読みもしない）、屋台の写真（権利が別）、廃業した屋台、避難所（別扱い）、
    公衆浴場の「個室付（風営法・エステ）」、許可の期限が切れた飲食店。
  - 住所だけの一覧（旅館業・公衆浴場・特定建築物・飲食店営業許可）は、国土地理院の住所検索で座標を出す。番地まで引けたものだけ使い、
    引けなかったものは出さない。座標は目安なので detail.geocode に残す。すでに保存済みの候補（source_key）は再び引かない。
"""
import argparse
import csv
import hashlib
import io
import json
import re
import time
import unicodedata
from datetime import date
from pathlib import Path

import requests

from . import addition_candidates as ac
from .db import connect, job

UA = "fukuoka-locadb/2.0 (https://github.com/mandrill-fukuoka/fukuoka-locadb; location research; BODIK CC BY)"
INTERVAL_S = 1.1
CKAN = "https://data.bodik.jp/api/3/action/package_show"
DATASET_PAGE = "https://data.bodik.jp/dataset/"
GSI = "https://msearch.gsi.go.jp/address-search/AddressSearch"
BBOX = ac.BBOX
LICENSE = "CC BY 4.0"

# kind：table（座標つきCSV）/ address（住所だけのCSV）/ shape（公園のShape）
# name / lat / lon / addr：列名の候補（最初に値のあるもの）。cat：種別の案（なければ名前の語から決める）。
# 氏名・届出者・連絡先の列は、ここに書かないかぎり読まない
SPECS = [
    dict(key="public_facility", pkg="401307_ftoshiken_public_facility", label="公共施設一覧（福岡都市圏共通フォーマット）", kind="table",
         name=["名称"], lat=["緯度"], lon=["経度"], addr=["住所"], cat="civic", detail=[]),
    dict(key="facility", pkg="401307_compatibleformat-facility", label="市の施設（共通フォーマット）", kind="table", formats=["CSV"],
         name=["名称"], lat=["緯度"], lon=["経度"], addr=["住所表記"], cat="civic", detail=["種別"]),
    dict(key="hoiku", pkg="401307_fukuokacity_hoiku", label="保育所等一覧", kind="table",
         name=["名称"], lat=["緯度"], lon=["経度"], addr=["所在地_連結表記"], cat=None, detail=["種別"]),
    dict(key="school", pkg="401307_school", label="市立学校一覧", kind="table",
         name=["施設名"], lat=["緯度"], lon=["経度"], addr=["所在地"], cat="school", detail=[]),
    dict(key="kouminkan", pkg="401307_kouminkan", label="公民館", kind="table",
         name=["名称"], lat=["緯度"], lon=["経度"], addr=["住所"], cat="civic", detail=["分類"]),
    dict(key="shikuyakusho", pkg="401307_shikuyakusho", label="市役所・区役所・保健福祉センター", kind="table",
         name=["名称"], lat=["緯度"], lon=["経度"], addr=["住所"], cat="civic", detail=["分類"]),
    dict(key="shimincenter", pkg="401307_shimincenter", label="市民センター", kind="table",
         name=["名称"], lat=["緯度"], lon=["経度"], addr=["住所"], cat="civic", detail=["分類"]),
    dict(key="toshokan", pkg="401307_toshokan", label="図書館", kind="table",
         name=["名称"], lat=["緯度"], lon=["経度"], addr=["住所"], cat="civic", detail=["分類"]),
    dict(key="subway", pkg="401307_subway", label="地下鉄位置情報", kind="table",
         name=["名称"], lat=["緯度"], lon=["経度"], addr=["住所"], cat="station", detail=[]),
    dict(key="wifi", pkg="401307_ftoshiken_wifi_accesspoint", label="公衆無線LANアクセスポイント", kind="table",
         name=["名称"], lat=["緯度"], lon=["経度"], addr=["住所"], cat=None, detail=[]),
    dict(key="waterspot", pkg="401307_waterspotopendata", label="給水スポット", kind="table", formats=["CSV"],
         name=["名称"], lat=["緯度"], lon=["経度"], addr=["所在地_連結表記"], cat=None, detail=["設置位置"]),
    dict(key="yatai", pkg="401307_yataiopendata", label="屋台基本情報", kind="table", formats=["CSV"],
         name=["名称"], lat=["緯度"], lon=["経度"], addr=["所在地_連結表記"], cat="eatery_street",
         detail=["区名", "エリア", "カテゴリー"], skip=("開業／廃業フラグ", "1"), url_col="よかなびURL"),    # 写真の列は読まない
    dict(key="park", pkg="401307_tosikouen_shape", label="都市公園（Shape）", kind="shape", cat="park"),
    dict(key="bath", pkg="401307_kousyuyokujo_eigyoukyoka", label="公衆浴場営業許可施設一覧", kind="address", cat="commercial_facility",
         name=["施設名称"], addr=["施設所在地"], building=["施設ビル名"], detail=["営業の種類"],
         skip_prefix=("営業の種類", "個室付")),     # 個室付（風営法）・個室付（エステ）は取り込まない
    dict(key="inn", pkg="401307_ryokangyou_eigyoukyoka", label="旅館業営業許可施設一覧", kind="address", cat="commercial_facility",
         name=["施設名称"], addr=["施設所在地"], building=["施設ビル名"], detail=["旅館業の種別", "客室数", "定員数"]),
    dict(key="building", pkg="401307_tokuteikenchikubutsu", label="特定建築物一覧", kind="address", cat=None,
         name=["施設名称"], addr=["施設所在地"], building=[], detail=[]),
    dict(key="eatery", pkg="401307_insyokuteneigyoutoueigyoukyokasisetuitiran", label="飲食店営業等営業許可施設一覧", kind="address",
         cat="commercial_facility", name=["屋号"], addr=["営業所所在地"], building=["営業所所在地ビル名"], detail=["業種", "業態"],
         id_cols=["指令番号県記号", "指令番号発令記号", "指令番号（許可番号）"], expire_col="許可終了日"),
]
SPEC_BY_KEY = {s["key"]: s for s in SPECS}
CHOME = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15}


# ------------------------------------------------------------------ 取得
def http_get(url, params=None, session=requests, sleep=time.sleep):
    for attempt in range(3):
        sleep(INTERVAL_S)
        try:
            r = session.get(url, params=params, timeout=60, headers={"User-Agent": UA})
            if r.status_code == 200:
                return r
        except requests.RequestException:
            pass
        sleep(3 * (attempt + 1))
    return None


def package_resources(pkg: str, session=requests, sleep=time.sleep) -> list[dict]:
    r = http_get(CKAN, {"id": pkg}, session, sleep)
    if r is None:
        return []
    try:
        return (r.json().get("result") or {}).get("resources") or []
    except ValueError:
        return []


def decode(b: bytes) -> str:
    for enc in ("utf-8-sig", "cp932"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", "replace")


def read_table(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


# ------------------------------------------------------------------ 整形
def clean(v) -> str:
    """全角英数・空白を整える（名前の比較を既存の地点とそろえる）"""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", v or "")).strip()


def first(rec: dict, cols: list[str]) -> str:
    for c in cols:
        v = clean(rec.get(c))
        if v:
            return v
    return ""


def to_float(v):
    try:
        return float(clean(v))
    except ValueError:
        return None


def in_bbox(lat, lon) -> bool:
    return lat is not None and lon is not None and BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lon <= BBOX[3]


def display_name(name: str, key: str) -> str:
    name = re.sub(r"^福岡市地下鉄\s*", "", name) if key == "subway" else name
    return ac.clean_title(name)


def stable_id(*parts) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def normalize_address(addr: str) -> str:
    """許可一覧の住所（…二丁目１１番２４号）を住所検索向けに整える。ビル名・階・号室は除く"""
    a = clean(addr)
    a = re.sub(r"([一二三四五六七八九十]+)丁目", lambda m: f"{CHOME.get(m.group(1), m.group(1))}丁目", a)
    a = re.sub(r"番地?", "-", a)
    a = re.sub(r"号.*$", "", a)
    a = re.sub(r"[-ー]\d+階.*$", "", a)
    a = re.sub(r"\s.*$", "", a).rstrip("-")
    return a if a.startswith("福岡県") else "福岡県" + a


def precise_title(title: str) -> bool:
    """住所検索の結果が番地まで特定できている（町名だけに丸められていない）"""
    t = re.sub(r"([0-9０-９]+|[一二三四五六七八九十]+)丁目", "", title or "")
    return bool(re.search(r"[0-9０-９]", t))


def geocode(addr: str, session=requests, sleep=time.sleep):
    """国土地理院の住所検索。番地まで引けたときだけ (lat, lon)、だめなら None"""
    r = http_get(GSI, {"q": normalize_address(addr)}, session, sleep)
    if r is None:
        return None
    try:
        hits = r.json() or []
    except ValueError:
        return None
    for h in hits[:1]:
        title = (h.get("properties") or {}).get("title", "")
        lon, lat = (h.get("geometry") or {}).get("coordinates") or (None, None)
        if title.startswith("福岡県福岡市") and precise_title(title) and in_bbox(lat, lon):
            return lat, lon
    return None


HINT_FROM_NAME = {"public_facility", "facility", "hoiku", "wifi", "waterspot", "building"}   # 名前の語から種別の案を決める一覧


def spec_category(spec: dict, name: str):
    """種別の案：名前の語（駅・神社・学校…）が当たればそれ、なければ一覧の既定（なければ空欄＝人が選ぶ）"""
    if spec["key"] in HINT_FROM_NAME:
        return ac.suggest_category(name) or spec.get("cat")
    return spec.get("cat")


def sized_name(name: str, kind: str) -> str:
    """都市公園の名称（「青葉７号」など）に、公園とわかる語がなければ「公園」を足す案"""
    return name if re.search(r"(公園|緑地|広場|庭園|園|運動場|競技場)$", name) else name + "公園"


# ------------------------------------------------------------------ 一覧 → 項目
def items_from_table(spec: dict, records: list[dict], res_name: str, stats: dict) -> list[dict]:
    out, seen = [], set()
    for rec in records:
        if spec.get("skip") and clean(rec.get(spec["skip"][0])) == spec["skip"][1]:
            stats["skipped_closed"] = stats.get("skipped_closed", 0) + 1
            continue
        name = display_name(first(rec, spec["name"]), spec["key"])
        lat, lon = to_float(first(rec, spec["lat"])), to_float(first(rec, spec["lon"]))
        if not name or not in_bbox(lat, lon):
            stats["skipped_no_point"] = stats.get("skipped_no_point", 0) + 1
            continue
        detail = {c: clean(rec.get(c)) for c in spec["detail"] if clean(rec.get(c))}
        if res_name:
            detail["resource"] = res_name
        addr = first(rec, spec["addr"])
        if addr:
            detail["address"] = addr
        if spec.get("url_col") and clean(rec.get(spec["url_col"])).startswith("https://"):
            detail["url"] = clean(rec.get(spec["url_col"]))
        key = f"bodik:{spec['key']}:{stable_id(name, f'{lat:.5f}', f'{lon:.5f}')}"
        if key in seen:
            continue
        seen.add(key)
        out.append({"key": key, "name": name, "lat": lat, "lon": lon, "detail": detail, "rank": 0.0})
    return out


def items_from_address(spec: dict, records: list[dict], stats: dict, today: date | None = None) -> list[dict]:
    """座標のない一覧：座標はあとで引く。氏名の列は読まない"""
    today = today or date.today()
    out, seen = [], set()
    for rec in records:
        if spec.get("skip_prefix") and clean(rec.get(spec["skip_prefix"][0])).startswith(spec["skip_prefix"][1]):
            stats["skipped_excluded_type"] = stats.get("skipped_excluded_type", 0) + 1
            continue
        if spec.get("expire_col"):
            m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", clean(rec.get(spec["expire_col"])))
            if m and date(int(m[1]), int(m[2]), int(m[3])) < today:
                stats["skipped_expired"] = stats.get("skipped_expired", 0) + 1
                continue
        name = display_name(first(rec, spec["name"]), spec["key"])
        addr = first(rec, spec["addr"])
        if not name or not addr:
            stats["skipped_no_name"] = stats.get("skipped_no_name", 0) + 1
            continue
        ident = "-".join(clean(rec.get(c)) for c in spec.get("id_cols", [])) or stable_id(name, addr)
        key = f"bodik:{spec['key']}:{ident}"
        if key in seen:
            continue
        seen.add(key)
        detail = {c: clean(rec.get(c)) for c in spec["detail"] if clean(rec.get(c))}
        detail["address"] = addr + (" " + first(rec, spec.get("building", [])) if first(rec, spec.get("building", [])) else "")
        out.append({"key": key, "name": name, "addr": addr, "lat": None, "lon": None, "detail": detail, "rank": 0.0})
    return out


PARK_SQL = """
select st_y(p) as lat, st_x(p) as lon from (
  select ST_Transform(ST_PointOnSurface(ST_SetSRID(ST_GeomFromText(%s), 2444)), 4326) as p) t
"""


def items_from_shape(conn, shp: str, stats: dict) -> list[dict]:
    """都市公園のポリゴン（JGD2000 平面直角座標II系）→ 公園ごとに最大のポリゴンの内側の点。面積の大きい順に並ぶ"""
    import shapefile
    from shapely.geometry import shape
    rd = shapefile.Reader(shp, encoding="cp932")
    parks: dict[tuple, dict] = {}
    for sr in rd.iterShapeRecords():
        rec = dict(zip([f[0] for f in rd.fields[1:]], sr.record))
        name, ward, kind = clean(rec.get("公園名称")), clean(rec.get("区名")), clean(rec.get("公園種別名"))
        if not name:
            continue
        geom = shape(sr.shape.__geo_interface__)
        area = float(rec.get("供用面積") or 0)
        p = parks.setdefault((name, ward), {"name": name, "ward": ward, "kind": kind, "area": 0.0, "best": (0.0, None)})
        p["area"] += area
        if geom.area >= p["best"][0]:
            p["best"] = (geom.area, geom.wkt)
    out = []
    for (name, ward), p in parks.items():
        r = conn.execute(PARK_SQL, (p["best"][1],)).fetchone()
        if not in_bbox(r["lat"], r["lon"]):
            stats["skipped_no_point"] = stats.get("skipped_no_point", 0) + 1
            continue
        out.append({"key": f"bodik:park:{stable_id(name, ward)}", "name": sized_name(name, p["kind"]), "lat": r["lat"], "lon": r["lon"],
                    "detail": {"公園種別": p["kind"], "区名": p["ward"], "供用面積m2": round(p["area"])}, "rank": p["area"]})
    return out


# ------------------------------------------------------------------ 候補にする
DUP_SQL = """
select exists (select 1 from spot_additions a
                where a.name = %(name)s and a.status <> 'rejected'
                  and ST_DWithin(ST_SetSRID(ST_MakePoint(a.lon, a.lat), 4326)::geography,
                                 ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography, 200)) as dup
"""


def candidates(conn, spec: dict, items: list[dict], stats: dict, known: set | None = None, seen: set | None = None,
               session=requests, sleep=time.sleep) -> list[dict]:
    """項目を判定して候補の行にする。住所だけの項目は、保存済みでないものだけ座標を引く。
    seen：この実行で出した候補（名前・位置）。別の一覧に同じ施設があるときは先の1件だけ"""
    known = known or set()
    seen = seen if seen is not None else set()
    rows = []
    for it in items:
        if it["key"] in known:
            stats["already_saved"] = stats.get("already_saved", 0) + 1
            continue
        lat, lon = it["lat"], it["lon"]
        detail = dict(it["detail"])
        if lat is None:
            pt = geocode(it["addr"], session, sleep)
            if pt is None:
                stats["geocode_failed"] = stats.get("geocode_failed", 0) + 1
                continue
            lat, lon = pt
            detail["geocode"] = "国土地理院 住所検索（目安）"
        j = ac.judge(conn, {"lat": lat, "lon": lon}, it["name"])
        if j["area_code"] is None:
            stats["skipped_outside"] = stats.get("skipped_outside", 0) + 1
        elif j["near_m"] is not None and j["near_m"] <= ac.NEAR_SPOT_M:
            stats["skipped_near"] = stats.get("skipped_near", 0) + 1
        elif j["same_name"]:
            stats["skipped_same_name"] = stats.get("skipped_same_name", 0) + 1
        elif (it["name"], round(lat, 4), round(lon, 4)) in seen or conn.execute(DUP_SQL, {"name": it["name"], "lat": lat, "lon": lon}).fetchone()["dup"]:
            stats["skipped_dup_candidate"] = stats.get("skipped_dup_candidate", 0) + 1
        else:
            seen.add((it["name"], round(lat, 4), round(lon, 4)))
            detail["dataset_label"] = spec["label"]
            cat = spec_category(spec, it["name"])
            rows.append({"source": "bodik", "source_key": it["key"], "name": it["name"], "lat": round(lat, 6), "lon": round(lon, 6),
                         "area_code": j["area_code"], "suggest_cat": cat, "source_url": DATASET_PAGE + spec["pkg"],
                         "files": None, "samples": [], "near_name": j["near_name"], "near_m": j["near_m"],
                         "dataset": spec["key"], "detail": detail, "sort_rank": it["rank"]})
    return rows


INSERT_SQL = """
insert into spot_additions (source, source_key, name, lat, lon, area_code, suggest_cat, source_url, files, samples, near_name, near_m,
                            dataset, detail, sort_rank)
values (%(source)s, %(source_key)s, %(name)s, %(lat)s, %(lon)s, %(area_code)s, %(suggest_cat)s, %(source_url)s, %(files)s,
        %(samples)s::jsonb, %(near_name)s, %(near_m)s, %(dataset)s, %(detail)s::jsonb, %(sort_rank)s)
on conflict (source_key) do nothing
"""


def save(conn, rows: list[dict]) -> int:
    """保存（決めた候補は上書きしない）。新しく入った件数を返す"""
    before = conn.execute("select count(*) n from spot_additions").fetchone()["n"]
    with conn.cursor() as cur:
        cur.executemany(INSERT_SQL, [{**r, "samples": json.dumps(r["samples"], ensure_ascii=False),
                                       "detail": json.dumps(r["detail"], ensure_ascii=False)} for r in rows])
    conn.commit()
    return conn.execute("select count(*) n from spot_additions").fetchone()["n"] - before


def collect(conn, spec: dict, stats: dict, workdir: Path, session=requests, sleep=time.sleep) -> list[dict]:
    """1つのデータセットを取って、項目の一覧にする"""
    res = [r for r in package_resources(spec["pkg"], session, sleep) if r.get("url")]
    if spec["kind"] == "shape":
        import zipfile
        z = next((r for r in res if (r.get("format") or "").upper() == "ZIP"), None)
        got = http_get(z["url"], None, session, sleep) if z else None
        if got is None:
            stats["fetch_failed"] = stats.get("fetch_failed", 0) + 1
            return []
        d = workdir / "park"
        d.mkdir(parents=True, exist_ok=True)
        zipfile.ZipFile(io.BytesIO(got.content)).extractall(d)
        shp = next(d.rglob("*.shp"), None)
        return items_from_shape(conn, str(shp), stats) if shp else []
    formats = spec.get("formats") or ["CSV"]
    res = [r for r in res if (r.get("format") or "").upper() in formats]
    items = []
    for r in res:
        got = http_get(r["url"], None, session, sleep)
        if got is None:
            stats["fetch_failed"] = stats.get("fetch_failed", 0) + 1
            continue
        records = read_table(decode(got.content))
        part = items_from_table(spec, records, r.get("name") or "", stats) if spec["kind"] == "table" \
            else items_from_address(spec, records, stats)
        items += part
    stats[f"{spec['key']}_items"] = len(items)
    return items


def main(argv=None):
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default="", help="データセットの名前をカンマ区切り（例 park,toshokan）。空なら全部。" + "、".join(SPEC_BY_KEY))
    ap.add_argument("--limit", type=int, default=0, help="住所だけの一覧で、座標を引く件数の上限（0＝制限なし）")
    ap.add_argument("--out", default="bodik-out")
    a = ap.parse_args(argv)
    keys = [k.strip() for k in a.only.split(",") if k.strip()] or list(SPEC_BY_KEY)
    bad = [k for k in keys if k not in SPEC_BY_KEY]
    if bad:
        raise SystemExit(f"知らないデータセット：{bad}")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with connect() as conn, job(conn, "bodik_import", None) as stats:
        stats.update(dry_run=a.dry_run, datasets=keys)
        allrows, seen = [], set()
        for k in keys:
            spec = SPEC_BY_KEY[k]
            st: dict = {}
            items = collect(conn, spec, st, out)
            known = {r["source_key"] for r in conn.execute("select source_key from spot_additions where dataset = %s", (k,)).fetchall()}
            if spec["kind"] == "address" and a.limit:          # 座標を引くのは時間がかかるので、1回の件数を絞れる（保存済みは数えない）
                items = [i for i in items if i["key"] not in known][:a.limit]
            rows = candidates(conn, spec, items, st, known, seen)
            st["candidates"] = len(rows)
            if a.apply:
                st["saved_new"] = save(conn, rows)
            stats[k] = st
            allrows += rows
        with open(out / "bodik.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["データセット", "名前", "緯度", "経度", "区", "種別の案", "最寄りの地点", "距離m", "詳細", "出典"])
            for r in allrows:
                w.writerow([r["dataset"], r["name"], r["lat"], r["lon"], r["area_code"], r["suggest_cat"] or "", r["near_name"] or "",
                            round(r["near_m"] or 0), json.dumps(r["detail"], ensure_ascii=False), r["source_url"]])
        print(json.dumps({**stats}, ensure_ascii=False))


if __name__ == "__main__":
    main()
