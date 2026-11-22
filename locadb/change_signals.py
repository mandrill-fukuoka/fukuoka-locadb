"""変化の材料：OSM のタグに廃業・解体・工事の印が新しく付いた地点を「要確認」にする（DB変更292。AIなし・費用ゼロ）

  python -m locadb.change_signals            印を数え直して、増えた分を change_risks に入れる（初回は基準を覚えるだけ）
  python -m locadb.change_signals --dry-run  印のある地物の数だけ数える（書き込みなし）

pipeline の最後（変化リスク監視の前）で毎週流れる。Overture の消失は overture_places の取り込みが記録する。
人が一次資料で確認する前提で、ここでは「確認済み」にしない。
"""
import argparse
import json
import sys

from .db import connect, job


def run(run_id: int | None = None) -> dict:
    with connect() as conn, job(conn, "change_signals", run_id) as stats:
        r = conn.execute("select change_osm_marks_refresh() r").fetchone()["r"]
        stats.update(r)
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.dry_run:
        with connect() as conn:
            n = conn.execute("select count(*) n from features where kind='element' and tags is not null and ("
                             "tags->>'building' = 'construction' or tags ? 'construction' or tags ? 'end_date' "
                             "or exists (select 1 from jsonb_object_keys(tags) k where k like 'disused:%%' "
                             "or k like 'abandoned:%%' or k like 'demolished%%' or k like 'was:%%'))").fetchone()["n"]
            known = conn.execute("select count(*) n from feature_marks").fetchone()["n"]
        print(json.dumps({"dry_run": True, "features_with_marks": n, "remembered": known}, ensure_ascii=False))
        return 0
    print(json.dumps(run(), ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
