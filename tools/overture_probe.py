"""Overture Maps の店舗（places）が福岡市の範囲にどれだけあり、質はどうかを数える（読み取りだけ。DBは触らない）。

  python tools/overture_probe.py --out DIR [--release 2026-xx-xx.0]
  公開のS3（認証なし）から DuckDB で読む。範囲は福岡市7区をおおむね含む箱。
  数えるもの：総数・名前の有無・信頼度の分布・営業状態・種別（上位）・夜の店（バー・居酒屋等）・500m四方ごとの密度の分布・チェーン（brand）の割合
  利用条件：places は CDLA Permissive 2.0（OSM由来を含まない）。出典表示は Overture Maps Foundation。
"""
import argparse
import json
from pathlib import Path

import duckdb

BBOX = (130.20, 33.43, 130.52, 33.72)          # lon_min, lat_min, lon_max, lat_max（福岡市7区を含む箱）
NIGHT = ("bar", "pub", "nightclub", "izakaya", "karaoke", "cocktail_bar", "beer_bar", "wine_bar", "sake_bar", "lounge", "hostess")


def connect(release: str | None):
    con = duckdb.connect()
    con.execute("install httpfs; load httpfs; install spatial; load spatial;")
    con.execute("set s3_region='us-west-2'")
    if release:
        src = f"read_parquet('s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*', hive_partitioning=1)"
    else:
        con.execute("attach 'https://stac.overturemaps.org/latest.ddb' as overture (read_only)")
        src = "overture.place"
    return con, src


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="overture-out")
    ap.add_argument("--release", default="")
    a = ap.parse_args(argv)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    con, src = connect(a.release or None)
    cols = [r[0] for r in con.execute(f"describe select * from {src}").fetchall()]
    print("columns:", cols)
    print("types:", {r[0]: r[1][:200] for r in con.execute(f"describe select * from {src}").fetchall() if r[0] in ("taxonomy", "brand", "basic_category", "operating_status")})
    x0, y0, x1, y1 = BBOX
    where = f"bbox.xmin >= {x0} and bbox.xmax <= {x1} and bbox.ymin >= {y0} and bbox.ymax <= {y1}"
    CAT = "basic_category" if "basic_category" in cols else "categories.primary"
    try:
        con.execute(f"select taxonomy.primary from {src} limit 1"); TAX = "taxonomy.primary"
    except duckdb.Error:
        TAX = "cast(null as varchar)"
    con.execute(f"create temp table p as select id, names.primary as name, {CAT} as cat, {TAX} as tax, confidence, "
                f"{'operating_status' if 'operating_status' in cols else 'cast(null as varchar)'} as status, "
                f"{'brand.names.primary' if 'brand' in cols else 'cast(null as varchar)'} as brand, "
                f"bbox.xmin as lon, bbox.ymin as lat from {src} where {where}")
    q = lambda s: con.execute(s).fetchall()
    res = {"total": q("select count(*) from p")[0][0],
           "with_name": q("select count(*) from p where name is not null")[0][0],
           "with_category": q("select count(*) from p where cat is not null")[0][0],
           "conf": {k: v for k, v in q("select case when confidence>=0.9 then '0.9+' when confidence>=0.7 then '0.7-0.9' when confidence>=0.5 then '0.5-0.7' else '<0.5' end b, count(*) from p group by 1")},
           "status": {str(k): v for k, v in q("select status, count(*) from p group by 1")},
           "chain": q("select count(*) from p where brand is not null")[0][0],
           "top_cat": q("select cat, count(*) n from p group by 1 order by n desc limit 40"),
           "top_tax": q("select tax, count(*) n from p group by 1 order by n desc limit 40"),
           "night": q(f"select count(*) from p where cat in ({','.join(repr(c) for c in NIGHT)}) or tax in ({','.join(repr(c) for c in NIGHT)})")[0][0]}
    # 500m四方（緯度0.0045度・経度0.0054度）ごとの店の数の分布
    cells = q("select floor(lat/0.0045) la, floor(lon/0.0054) lo, count(*) n from p where confidence>=0.5 group by 1,2")
    ns = sorted(c[2] for c in cells)
    pct = lambda f: ns[min(len(ns) - 1, int(len(ns) * f))] if ns else 0
    res["cells"] = {"n_cells": len(ns), "p50": pct(0.5), "p90": pct(0.9), "p99": pct(0.99), "max": ns[-1] if ns else 0}
    top = sorted(cells, key=lambda c: -c[2])[:8]
    res["top_cells"] = [{"lat": round((c[0] + .5) * 0.0045, 4), "lon": round((c[1] + .5) * 0.0054, 4), "n": c[2]} for c in top]
    (out / "overture.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    L = ["| 項目 | 値 |", "|---|---:|", f"| 範囲内の件数 | {res['total']:,} |", f"| 名前あり | {res['with_name']:,} |",
         f"| 種別あり | {res['with_category']:,} |", f"| チェーン（brandあり） | {res['chain']:,} |", f"| 夜の店（バー等） | {res['night']:,} |"]
    L += [f"| 信頼度 {k} | {v:,} |" for k, v in sorted(res["conf"].items())]
    L += [f"| 営業状態 {k} | {v:,} |" for k, v in res["status"].items()]
    L += ["", f"500m四方 {res['cells']['n_cells']} 区画（信頼度0.5以上）：中央値 {res['cells']['p50']}・90% {res['cells']['p90']}・99% {res['cells']['p99']}・最大 {res['cells']['max']}", "",
          "上位の区画（緯度・経度・件数）：" + "、".join(f"({c['lat']},{c['lon']}) {c['n']}" for c in res["top_cells"]), "",
          "種別 basic_category（上位40）：" + "、".join(f"{c}:{n}" for c, n in res["top_cat"]), "",
          "種別 taxonomy.primary（上位40）：" + "、".join(f"{c}:{n}" for c, n in res["top_tax"])]
    (out / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
