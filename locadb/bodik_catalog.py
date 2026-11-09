"""BODIK ODCS の福岡市（組織 401307）のデータセットを棚卸しする（読み取り専用。DBにも本番にも触れない）

  python -m locadb.bodik_catalog [--out DIR]    全データセットの一覧（catalog.csv）と、使い道の見立て（catalog-summary.json）を出す

どのデータセットを取り込み済みか（bodik_import の SPECS・bodik_aed）も付ける。取り込んでいないものは、形式・ライセンス・列名から
「位置があるか」「CSVか」を機械的に見立てる。見立ては目安で、使うかどうかは人が決める。連絡先・氏名の列は値を見ない（列名だけ）。
"""
import argparse
import csv
import json
import time
from pathlib import Path

import requests

from . import bodik_import as bi

SEARCH = "https://data.bodik.jp/api/3/action/package_search"
ORG = "401307"
ROWS = 100
USED = {s["pkg"]: s["label"] for s in bi.SPECS} | {"401307_aed": "AED（近くのAED）"}
POINT_WORDS = ("緯度", "経度", "lat", "lon", "住所", "所在地", "位置")


def all_packages(session=requests, sleep=time.sleep) -> list[dict]:
    out, start = [], 0
    while True:
        r = bi.http_get(SEARCH, {"q": "*:*", "fq": f"organization:{ORG}", "rows": ROWS, "start": start, "sort": "name asc"}, session, sleep)
        if r is None:
            raise SystemExit(f"取得に失敗しました（start={start}）")
        res = r.json().get("result") or {}
        got = res.get("results") or []
        out += got
        start += ROWS
        if not got or start >= (res.get("count") or 0):
            return out


def summarize(pkg: dict) -> dict:
    res = pkg.get("resources") or []
    fmts = sorted({(r.get("format") or "").upper().lstrip(".") for r in res if r.get("format")})
    return {"name": pkg.get("name"), "title": (pkg.get("title") or "").replace("　", " "),
            "notes": " ".join((pkg.get("notes") or "").split())[:100], "license": pkg.get("license_id") or pkg.get("license_title") or "",
            "modified": (pkg.get("metadata_modified") or "")[:10], "formats": "/".join(fmts), "resources": len(res),
            "tags": "・".join(t.get("name", "") for t in (pkg.get("tags") or []))[:80],
            "used": USED.get(pkg.get("name"), "")}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="bodik-catalog-out")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pkgs = all_packages()
    rows = [summarize(p) for p in pkgs]
    with open(out / "catalog.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    fmt_count: dict[str, int] = {}
    for r in rows:
        for f in (r["formats"].split("/") if r["formats"] else ["（なし）"]):
            fmt_count[f] = fmt_count.get(f, 0) + 1
    lic: dict[str, int] = {}
    for r in rows:
        lic[r["license"]] = lic.get(r["license"], 0) + 1
    summary = {"datasets": len(rows), "used": sum(1 for r in rows if r["used"]), "formats": fmt_count, "licenses": lic,
               "csv_unused": sum(1 for r in rows if not r["used"] and "CSV" in r["formats"].split("/")),
               "missing_used": sorted(set(USED) - {r["name"] for r in rows})}
    (out / "catalog-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
