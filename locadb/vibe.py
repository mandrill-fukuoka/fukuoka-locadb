"""雰囲気の目安（流れ検索の精度改善 段階2、DB変更210）：周りの店の数・タグの当てはまり度・景観らしさを付け直す。

計算は sql/api/85_vibe.sql（vibe_apply・vibe_signals・group_typicality）。AIは使わない。
自動収集の空間特徴・ルールのタグの後に呼ぶ。単独でも実行できる：

  python -m locadb.vibe --dry-run   # 分布だけ（書き込みなし）
  python -m locadb.vibe --apply     # 付け直す
"""
import argparse
import json

from .db import connect, job


def stats() -> dict:
    with connect() as conn:
        return conn.execute("select vibe_stats() r").fetchone()["r"]


def run(run_id: int | None = None) -> dict:
    with connect() as conn, job(conn, "vibe", run_id) as st:
        r = conn.execute("select vibe_apply() r").fetchone()["r"]
        conn.commit()
        st.update({"updated": r.get("updated"), "version": r.get("version")})
        print("    雰囲気の目安 " + json.dumps({"updated": r.get("updated"), "version": r.get("version")}, ensure_ascii=False))
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    r = run() if a.apply else stats()
    print("```json\n" + json.dumps(r, ensure_ascii=False, indent=1, default=str) + "\n```")


if __name__ == "__main__":
    main()
