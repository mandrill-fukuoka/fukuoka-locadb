"""BODIK のデータセットの「中身」を読み取りだけで確かめる（DBにも本番にも触れない）。

  python -m locadb.bodik_probe --pkg 401307_tosikouen,401307_shieitosen2025 [--out DIR]

XLSX / CSV はシートごとに列名・埋まっている件数・先頭の数行、ZIP は中のファイル名と
（GTFS なら）stops.txt の先頭を出す。XLS（旧形式）は読めないので、その旨だけ書く。
最後の行に要約の JSON を出す。
"""
from __future__ import annotations

import argparse
import io
import json
import time
import zipfile
from pathlib import Path

import requests

from . import bodik_import as bi

SAMPLE_ROWS = 3
MAX_COLS = 60


def profile_rows(rows: list[list], n: int = SAMPLE_ROWS) -> dict:
    """1行目を見出し、その下を本体として、列ごとの埋まり具合と見本を返す"""
    rows = [r for r in rows if any(bi.clean(str(c)) for c in r if c is not None)]
    if not rows:
        return {"rows": 0, "columns": []}
    head = [bi.clean(str(c)) if c is not None else "" for c in rows[0]][:MAX_COLS]
    body = rows[1:]
    cols = []
    for i, h in enumerate(head):
        vals = [bi.clean(str(r[i])) for r in body if i < len(r) and r[i] is not None and bi.clean(str(r[i]))]
        cols.append({"name": h, "filled": len(vals), "sample": vals[:n]})
    return {"rows": len(body), "columns": cols}


def probe_xlsx(content: bytes) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    return {ws.title: profile_rows([list(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets}


def probe_csv(content: bytes) -> dict:
    import csv
    text = bi.decode(content)
    return {"csv": profile_rows([r for r in csv.reader(io.StringIO(text))])}


def probe_zip(content: bytes) -> dict:
    import csv
    z = zipfile.ZipFile(io.BytesIO(content))
    out = {"files": [f"{i.filename} ({i.file_size}B)" for i in z.infolist()][:80]}
    for name in z.namelist():
        base = name.rsplit("/", 1)[-1]
        if base in ("stops.txt", "agency.txt", "routes.txt"):
            text = bi.decode(z.read(name))
            out[base] = profile_rows([r for r in csv.reader(io.StringIO(text))], n=20 if base == "stops.txt" else SAMPLE_ROWS)
    return out


def probe_resource(res: dict, session=requests, sleep=time.sleep) -> dict:
    fmt = (res.get("format") or "").upper()
    info = {"name": res.get("name"), "format": fmt}
    if fmt not in ("XLSX", "CSV", "ZIP"):
        info["note"] = "読み取り対象外の形式（XLS・PDF など）"
        return info
    got = bi.http_get(res["url"], None, session, sleep)
    if got is None:
        info["note"] = "取得できなかった"
        return info
    try:
        info["content"] = {"XLSX": probe_xlsx, "CSV": probe_csv, "ZIP": probe_zip}[fmt](got.content)
    except Exception as e:  # 形式が崩れていても他の資源を続ける
        info["note"] = f"読み取りに失敗: {type(e).__name__}"
    return info


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkg", required=True, help="データセット名をカンマ区切り")
    ap.add_argument("--out", default="bodik-probe-out")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    result = {}
    for pkg in [p.strip() for p in a.pkg.split(",") if p.strip()]:
        result[pkg] = [probe_resource(r) for r in bi.package_resources(pkg) if r.get("url")]
    (out / "probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    for pkg, items in result.items():
        print(f"== {pkg}")
        for it in items:
            print(f"  [{it['format']}] {it.get('name')} {it.get('note', '')}")
            for sheet, p in (it.get("content") or {}).items():
                if isinstance(p, dict) and "columns" in p:
                    print(f"    - {sheet}: {p['rows']}行")
                    for c in p["columns"]:
                        print(f"        {c['name']}: {c['filled']}件 例={c['sample'][:2]}")
                else:
                    print(f"    - {sheet}: {p if not isinstance(p, list) else p[:20]}")
    print(json.dumps({pkg: len(items) for pkg, items in result.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
