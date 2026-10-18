"""ルールで付けるタグ（タスクI）：位置・種別・空間特徴から機械的に付ける。AIは使わない。

規則は sql/api/80_rule_tags.sql の rule_tags_apply。ここは実行して記録を残すだけ。
空間特徴（spatial）のあとに呼ぶ（空間特徴が古い・無い地点には、種別だけで付く規則だけが効く）。
"""
import json

from .db import connect, job


def run(run_id: int | None = None) -> dict:
    with connect() as conn, job(conn, "rule_tags", run_id) as stats:
        st = conn.execute("select rule_tags_apply() r").fetchone()["r"]
        conn.commit()
        stats.update(st)
        print("    ルールのタグ " + json.dumps(st, ensure_ascii=False))
    return st
