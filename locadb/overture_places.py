"""Overture Maps の店舗（places）を福岡市の範囲で取り込み、地点の「周りの店の数」に使う（DB変更290。AIなし・費用ゼロ）

  python -m locadb.overture_places --dry-run [--release 2026-xx-xx.0] [--out DIR]   読み取りのみ。件数と種別の内訳を出す
  python -m locadb.overture_places --apply   [--release ...]                         place_points を作り直し、density_refresh と vibe_apply を流す

- データ：Overture Maps Foundation の places（CDLA Permissive 2.0。OSM由来を含まない）。公開のS3から DuckDB で読む（認証なし）。
- 使う列は位置・種別・信頼度・ID・営業状態だけ。名前・電話・URL・住所は保存しない。信頼度0.5未満と、閉業の印があるものは捨てる。
- 地点にはしない。地点の「周りの店の数」（vibe の材料）と「人通りの目安」（半径500mの数）に使う。
- 件数が今の半分未満・極端に少ないときは取得の失敗とみて作り直さない。
- 画面の出典：Overture Maps Foundation（NOTICE.md に記載）。
"""
import argparse
import json
import sys
from pathlib import Path

from .db import connect, job

BBOX = (130.20, 33.43, 130.52, 33.72)          # lon_min, lat_min, lon_max, lat_max（福岡市7区を含む箱）
MIN_CONF = 0.5
MIN_POINTS = 20000                              # これ未満なら取得の失敗とみる（2026-11 の試算では約5.8万件・信頼度0.5以上は約5.2万件）
MIN_KEEP_RATIO = 0.5

NIGHT = {"bar", "pub", "nightclub", "izakaya", "karaoke", "cocktail_bar", "beer_bar", "wine_bar", "sake_bar", "lounge",
         "hostess_club", "beer_garden", "gay_bar", "dive_bar", "sports_bar", "hookah_bar", "tiki_bar"}
EAT_BASIC = {"restaurant", "casual_eatery", "cafe", "coffee_shop"}
EAT_TAX = {"cafe", "coffee_shop", "bakery", "dessert_shop", "fast_food_restaurant", "ice_cream_shop", "tea_room", "juice_bar",
           "food_court", "diner", "bistro", "ramen_restaurant"}
RETAIL_EXTRA = {"shopping", "pharmacy_and_drug_store", "convenience_store", "grocery_store", "supermarket", "department_store",
                "shopping_mall", "pharmacy", "drugstore"}
SKIP = {"atm", "parking", "ev_charging_station", "rental_service", "rental_kiosk"}      # 人の賑わいの目安にならない


def classify(basic: str | None, tax: str | None) -> str | None:
    """bar / eat / retail / other。数えないもの（ATM・駐車場など）は None"""
    b, t = (basic or "").strip(), (tax or "").strip()
    if b in SKIP or t in SKIP:
        return None
    if b == "bar" or t in NIGHT:
        return "bar"
    if b in EAT_BASIC or t in EAT_TAX or t.endswith("_restaurant"):
        return "eat"
    if b.endswith("_store") or b in RETAIL_EXTRA or t in RETAIL_EXTRA or t.endswith("_store") or t.endswith("_shop"):
        return "retail"
    return "other"


def latest_release() -> str:
    """Overture の STAC カタログが示す最新のリリース名。取れなければ空"""
    import requests
    try:
        return str(requests.get("https://stac.overturemaps.org/catalog.json", timeout=30).json().get("latest") or "")
    except Exception:  # noqa: BLE001
        return ""


def fetch(release: str | None = None, con=None) -> tuple[list[tuple], str]:
    """(gers_id, cls, cat, conf, lon, lat) の一覧と、使ったリリース名"""
    import duckdb
    release = release or latest_release() or None
    con = con or duckdb.connect()
    con.execute("install httpfs; load httpfs;")
    con.execute("set s3_region='us-west-2'")
    if release:
        src = f"read_parquet('s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*', hive_partitioning=1)"
    else:
        con.execute("install spatial; load spatial;")
        con.execute("attach 'https://stac.overturemaps.org/latest.ddb' as overture (read_only)")
        src = "overture.place"
    cols = {r[0] for r in con.execute(f"describe select * from {src}").fetchall()}
    cat = "basic_category" if "basic_category" in cols else "categories.primary"
    tax = "taxonomy.primary" if "taxonomy" in cols else "cast(null as varchar)"
    st = "operating_status" if "operating_status" in cols else "cast(null as varchar)"
    x0, y0, x1, y1 = BBOX
    rows = con.execute(f"select id, {cat}, {tax}, confidence, {st}, bbox.xmin, bbox.ymin from {src} "
                       f"where bbox.xmin >= {x0} and bbox.xmax <= {x1} and bbox.ymin >= {y0} and bbox.ymax <= {y1} "
                       f"and confidence >= {MIN_CONF}").fetchall()
    out = []
    for gid, basic, taxp, conf, status, lon, lat in rows:
        if status and status not in ("open", "temporarily_closed") and "closed" in status:
            continue                                   # permanently_closed などは捨てる
        cls = classify(basic, taxp)
        if cls:
            out.append((gid, cls, basic, float(conf), float(lon), float(lat)))
    rel = release or ""
    if not rel:
        try:
            rel = str(con.execute("select release from overture.place limit 1").fetchone()[0])
        except Exception:  # noqa: BLE001
            rel = "latest"
    return out, rel


def replace_points(conn, rows: list[tuple], release: str) -> int:
    with conn.transaction():
        conn.execute("delete from place_points")
        with conn.cursor() as cur:
            with cur.copy("copy place_points (gers_id, cls, cat, conf, geom, release) from stdin") as cp:
                for gid, cls, cat, conf, lon, lat in rows:
                    # geography の COPY は WKT 文字列で渡す（SRID は列の型で決まる）
                    cp.write_row((gid, cls, cat, conf, f"SRID=4326;POINT({lon} {lat})", release))
    return len(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--release", default="")
    ap.add_argument("--out", default="overture-out")
    a = ap.parse_args(argv)
    rows, rel = fetch(a.release or None)
    by = {}
    for r in rows:
        by[r[1]] = by.get(r[1], 0) + 1
    stats = {"dry_run": a.dry_run, "release": rel, "points": len(rows), **{f"cls_{k}": v for k, v in sorted(by.items())}}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    with connect() as conn, job(conn, "overture_places") as st:
        before = conn.execute("select count(*) n from place_points").fetchone()["n"]
        stats["before"] = before
        if len(rows) < MIN_POINTS:
            raise SystemExit(f"取り込む件数（{len(rows)}）が少なすぎるので止めました（取得の失敗かも。下限 {MIN_POINTS}）")
        if a.apply and before and len(rows) < before * MIN_KEEP_RATIO:
            raise SystemExit(f"取り込む件数（{len(rows)}）が今の件数（{before}）の半分未満なので止めました（取得の失敗かも）")
        if a.apply:
            replace_points(conn, rows, rel)
            stats["spots_refreshed"] = conn.execute("select density_refresh() n").fetchone()["n"]
            stats["vibe"] = {k: v for k, v in conn.execute("select vibe_apply() v").fetchone()["v"].items()
                             if k in ("updated", "version")}
            stats.update({k: v for k, v in conn.execute("select density_stats() d").fetchone()["d"].items()
                          if k.endswith("p10_50_90_99")})
        st.update(stats)
    (Path(a.out) / "overture.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
