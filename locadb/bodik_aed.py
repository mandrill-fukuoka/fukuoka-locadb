"""福岡市のAED設置場所（BODIK ODCS「福岡市 AED」、CC BY 4.0）を取り込み、地点ページの「近くのAED」に使う（AIなし・費用ゼロ）

  python -m locadb.bodik_aed --dry-run [--out DIR]    読み取りのみ。取り込みを試して取り消し、一覧（aed.csv）と項目名を出す
  python -m locadb.bodik_aed --apply                  aed_points に取り込む（地点ページにすぐ出る。DB変更230が先）

データ元：福岡市「AED設置施設登録制度」に登録した施設の一覧。CKAN の package_search で「福岡市 AED」を探し、CSV を読む（--pkg で固定もできる）。
  - 読む列：名称・住所・緯度・経度・設置位置（設置場所）・利用可能な曜日や時間・備考。電話番号・担当者・法人番号などは読まない。
  - 座標がない行は、国土地理院の住所検索で番地まで引けたものだけ使う（geocoded=true。位置は目安）。
  - 取り込みのたびに、一覧にないもの（登録の取り下げ）は active=false にする。同じ内容を何度取り込んでも同じ結果。
  - 登録時点の情報で、実際に使えるか・使える時間は現地で確かめる必要がある。撮影の可否・安全は保証しない（画面に明記）。
"""
import argparse
import csv
import json
import re
import time
import unicodedata
from pathlib import Path

import requests

from . import bodik_import as bi
from .db import connect, job

SEARCH = "https://data.bodik.jp/api/3/action/package_search"
DATASET_PAGE = bi.DATASET_PAGE
ORG = "401307"
MIN_KEEP_RATIO = 0.5                       # 前回の件数のこの割合を下回るときは、取得の失敗とみて取り込まない

NAME_COLS = ["名称", "施設名", "施設名称", "設置施設名"]
ADDR_COLS = ["住所", "所在地", "所在地_連結表記"]
LAT_COLS, LON_COLS = ["緯度"], ["経度"]
PLACE_COLS = ["フリースペース", "設置位置"]      # 福岡市のデータは「設置場所:… 使用時間:…」を「フリースペース」に続けて書いている（「設置場所」の列は別の値）
HOURS_COLS = ["利用可能曜日", "利用可能開始時間", "利用可能終了時間", "利用可能日時特記事項", "使用時間", "利用時間"]
REMARK_COLS = ["備考"]


def find_package(session=requests, sleep=time.sleep, pkg: str = "") -> dict | None:
    """「福岡市 AED」のデータセット（package_show の結果）。pkg を指定すればそれを使う"""
    if pkg:
        r = bi.http_get(bi.CKAN, {"id": pkg}, session, sleep)
        return (r.json().get("result") if r is not None else None) or None
    r = bi.http_get(SEARCH, {"q": "AED", "fq": f"organization:{ORG}", "rows": 20}, session, sleep)
    if r is None:
        return None
    hits = (r.json().get("result") or {}).get("results") or []
    cands = [h for h in hits if "aed" in unicodedata.normalize("NFKC", h.get("title") or "").lower()]
    cands.sort(key=lambda h: (unicodedata.normalize("NFKC", h.get("title") or "").replace(" ", "") != "福岡市AED", h.get("title") or ""))
    return cands[0] if cands else None


def license_label(pkg: dict) -> str:
    """CKAN のライセンス表記 → 画面に出す名前。cc-by は福岡市の他の一覧と同じ「CC BY 4.0」。それ以外はデータの表記のまま"""
    lid = (pkg.get("license_id") or "").lower()
    return "CC BY 4.0" if lid in ("cc-by", "") else (pkg.get("license_title") or lid)


PROFILE_COLS = ["設置場所", "概要", "開催日時", "フリースペース", "備考", "承認状態", "公開範囲", "大分類__", "中分類__", "小分類__"]


def column_profile(records: list[dict], n: int = 3) -> dict:
    """列の中身をつかむための確認用（読み取りのみ）：埋まっている件数と、値の例。連絡先の列は見ない"""
    out = {}
    for c in PROFILE_COLS:
        vals = [bi.clean(r.get(c)) for r in records if bi.clean(r.get(c))]
        if not vals:
            continue
        uniq = sorted(set(vals))
        out[c] = {"filled": len(vals), "distinct": len(uniq), "examples": [v[:120] for v in (uniq if len(uniq) <= 6 else vals[:n])]}
    return out


def join_hours(rec: dict) -> str:
    parts = []
    for c in HOURS_COLS:
        v = bi.clean(rec.get(c))
        if v and v not in parts:
            parts.append(f"{c.replace('利用可能', '')}：{v}" if c.startswith("利用可能") else v)
    return " ".join(parts)


def strip_place_label(text: str) -> str:
    return re.sub(r"^(AED)?設置場所\s*[：:]\s*", "", text.strip().rstrip(" ,、，")).strip()


def split_place(text: str) -> tuple[str, str]:
    """福岡市のデータは設置位置に「設置場所：…, 使用時間：…」と続けて書かれていることがある。分けられれば分ける"""
    m = re.search(r"使用時間\s*[：:]\s*(.*)$", text)
    if not m:
        return strip_place_label(text), ""
    return strip_place_label(text[:m.start()]), m.group(1).strip()


def points_from_records(records: list[dict], stats: dict, session=requests, sleep=time.sleep, geocode_limit: int = 0) -> list[dict]:
    out, seen, geocoded = [], set(), 0
    for rec in records:
        name = bi.display_name(bi.first(rec, NAME_COLS), "aed")
        addr = bi.first(rec, ADDR_COLS)
        if not name:
            stats["skipped_no_name"] = stats.get("skipped_no_name", 0) + 1
            continue
        lat, lon = bi.to_float(bi.first(rec, LAT_COLS)), bi.to_float(bi.first(rec, LON_COLS))
        approx = False
        if not bi.in_bbox(lat, lon):
            if not addr or (geocode_limit and geocoded >= geocode_limit):
                stats["skipped_no_point"] = stats.get("skipped_no_point", 0) + 1
                continue
            geocoded += 1
            pt = bi.geocode(addr, session, sleep)
            if pt is None:
                stats["geocode_failed"] = stats.get("geocode_failed", 0) + 1
                continue
            (lat, lon), approx = pt, True
            stats["geocoded"] = stats.get("geocoded", 0) + 1
        place, hours = split_place(bi.first(rec, PLACE_COLS))
        extra = join_hours(rec)
        hours = " ".join(x for x in (hours, extra) if x)
        remark = bi.first(rec, REMARK_COLS)
        if remark:
            hours = (hours + " " if hours else "") + remark
        key = f"bodik:aed:{bi.stable_id(name, addr, place)}"
        if key in seen:
            stats["duplicates"] = stats.get("duplicates", 0) + 1
            continue
        seen.add(key)
        out.append({"source_key": key, "name": name, "address": addr, "place_note": place[:300], "hours_note": hours[:300],
                    "lat": lat, "lon": lon, "geocoded": approx})
    return out


UPSERT = """
insert into aed_points (source_key, name, address, place_note, hours_note, geom, geocoded, active, source_url, source_title, license, fetched_at)
values (%(source_key)s, %(name)s, %(address)s, nullif(%(place_note)s, ''), nullif(%(hours_note)s, ''),
        ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography, %(geocoded)s, true, %(source_url)s, %(source_title)s, %(license)s, now())
on conflict (source_key) do update set name = excluded.name, address = excluded.address, place_note = excluded.place_note,
  hours_note = excluded.hours_note, geom = excluded.geom, geocoded = excluded.geocoded, active = true,
  source_url = excluded.source_url, source_title = excluded.source_title, license = excluded.license, fetched_at = now()
"""


def run_import(conn, points: list[dict], source: dict, apply: bool) -> dict:
    """aed_points に入れ、一覧にないものを active=false にする。dry-run は入れてから取り消す"""
    import psycopg
    res = {"upserted": 0, "deactivated": 0, "committed": False}
    if not conn.execute("select to_regclass('public.aed_points') t").fetchone()["t"]:
        res["skipped"] = "aed_points がまだありません（db-migrate の 230 が先）"
        if apply:
            raise SystemExit(res["skipped"])
        return res
    before = conn.execute("select count(*) n from aed_points where active").fetchone()["n"]
    if apply and before and len(points) < before * MIN_KEEP_RATIO:
        raise SystemExit(f"取り込む件数（{len(points)}）が今の件数（{before}）の半分未満なので止めました（取得の失敗かもしれません）")
    with conn.transaction():
        for p in points:
            conn.execute(UPSERT, {**p, **source})
            res["upserted"] += 1
        keys = [p["source_key"] for p in points]
        res["deactivated"] = conn.execute(
            "update aed_points set active = false where active and not (source_key = any(%s))", (keys,)).rowcount
        res["active_after"] = conn.execute("select count(*) n from aed_points where active").fetchone()["n"]
        if not (apply and points):
            raise psycopg.Rollback()
    res["committed"] = bool(apply and points)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--pkg", default="", help="データセットのID（空なら検索して決める）")
    ap.add_argument("--limit", type=int, default=0, help="座標のない行で、住所検索する件数の上限（0＝制限なし）")
    ap.add_argument("--out", default="aed-out")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with connect() as conn, job(conn, "bodik_aed", None) as stats:
        stats["dry_run"] = a.dry_run
        pkg = find_package(pkg=a.pkg)
        if not pkg:
            raise SystemExit("福岡市のAEDのデータセットが見つかりません（--pkg で指定してください）")
        name = pkg.get("name") or a.pkg
        stats["package"] = {"name": name, "title": pkg.get("title"), "license": pkg.get("license_title") or pkg.get("license_id"),
                            "modified": pkg.get("metadata_modified")}
        res_list = [r for r in (pkg.get("resources") or []) if r.get("url") and (r.get("format") or "").upper() == "CSV"]
        stats["csv_resources"] = [r.get("name") for r in res_list]
        records = []
        for r in res_list:
            got = bi.http_get(r["url"])
            if got is not None:
                records += bi.read_table(bi.decode(got.content))
        stats["records"] = len(records)
        stats["columns"] = sorted({k for rec in records[:50] for k in rec})
        stats["column_profile"] = column_profile(records)
        points = points_from_records(records, stats, geocode_limit=a.limit)
        stats["points"] = len(points)
        source = {"source_url": DATASET_PAGE + name, "source_title": f"BODIK ODCS 福岡市「{(pkg.get('title') or 'AED').replace('福岡市 ', '').replace('福岡市', '').strip() or 'AED'}」",
                  "license": license_label(pkg)}
        stats["source"] = source
        res = run_import(conn, points, source, a.apply)
        stats["import"] = res
        with open(out / "aed.csv", "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["名称", "住所", "設置位置", "利用時間など", "緯度", "経度", "座標は住所から"])
            for p in points:
                w.writerow([p["name"], p["address"], p["place_note"], p["hours_note"], f"{p['lat']:.6f}", f"{p['lon']:.6f}", "はい" if p["geocoded"] else ""])
        print(json.dumps({**stats}, ensure_ascii=False))


if __name__ == "__main__":
    main()
