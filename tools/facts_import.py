#!/usr/bin/env python3
"""承認済みの事実（data/facts/*.json）を facts_import で取り込む。

  DATABASE_URL=… python tools/facts_import.py data/facts/2026-10-18.json            # 試し（既定。取り込んでから取り消す）
  DATABASE_URL=… python tools/facts_import.py data/facts/2026-10-18.json --apply    # 取り込む

- 1回のトランザクション。弾かれた行（rejected）が1件でもあれば、全体を取り消して終了コード1で止まる
- 同じ内容がすでに承認済みなら skipped（何度実行しても同じ結果）
- 承認者は JSON の approved_by。AIは使わない
"""
import argparse
import json
import os
import sys

import psycopg

BATCH = 200


def run(path, apply=False, url=None):
    doc = json.load(open(path, encoding="utf-8"))
    items, user = doc["items"], doc["approved_by"]
    total = {"added": 0, "skipped": 0, "rejected": []}
    with psycopg.connect(url or os.environ["DATABASE_URL"]) as conn:
        for i in range(0, len(items), BATCH):
            r = conn.execute("select facts_import(%s::jsonb, %s)", (json.dumps(items[i:i + BATCH], ensure_ascii=False), user)).fetchone()[0]
            total["added"] += r["added"]; total["skipped"] += r["skipped"]; total["rejected"] += r["rejected"]
        ok = not total["rejected"]
        if apply and ok:
            conn.commit()
        else:
            conn.rollback()
    total.update(mode="apply" if apply and ok else "dry_run", input=len(items), committed=bool(apply and ok))
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    res = run(a.file, a.apply)
    print(json.dumps(res, ensure_ascii=False))
    if res["rejected"]:
        print("弾かれた行があるため、取り込まずに止めました", file=sys.stderr)
        sys.exit(1)
