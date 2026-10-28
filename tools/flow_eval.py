"""流れ検索の確認（読み取りのみ）：確認用シーン文で build_routes を呼び、上位の流れと点数を表にする

  DATABASE_URL=... python tools/flow_eval.py [--scenes data/flow_eval/scenes.txt] [--json out.json] [--top 3]

シーン文の解釈は画面と同じ web/app/scene.php（キーワードによる簡易解釈）を php-cli で呼ぶ。
最後に取り消す（rollback）。検索の記録 record_route_search は呼ばない＝おすすめの流れは増えない。
（build_routes は検索ごとの一時表を作るので、読み取り専用のトランザクションでは動かない）
"""
import argparse
import json
import os
import statistics
import subprocess
import time
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
PHP = ("require 'web/app/scene.php'; echo json_encode(interpret_by_keywords($argv[1]), JSON_UNESCAPED_UNICODE);")


def interpret(scene: str) -> dict:
    out = subprocess.run(["php", "-r", PHP, scene], cwd=ROOT, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenes", default=str(ROOT / "data" / "flow_eval" / "scenes.txt"))
    ap.add_argument("--json")
    ap.add_argument("--top", type=int, default=3)
    a = ap.parse_args()
    scenes = [l.strip() for l in Path(a.scenes).read_text(encoding="utf-8").splitlines()
              if l.strip() and not l.lstrip().startswith("#")]
    results, matches = [], []
    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        # 画面（service_role）が検索ごとの一時表を作れるか
        tmp = c.execute("""select case when exists (select 1 from pg_roles where rolname = 'service_role')
                                  then has_database_privilege('service_role', current_database(), 'TEMP') end""").fetchone()[0]
        print(f"service_role の一時表の権限：{ {True: 'あり', False: 'なし（！画面の検索が失敗します）', None: '-'}[tmp] }")
        for i, scene in enumerate(scenes, 1):
            q = interpret(scene)
            t0 = time.time()
            rs = c.execute("select build_routes(%s, %s::jsonb, %s, null, null, 8)",
                           [q["sequence"], json.dumps(q["tags"]), q["travel_mode"]]).fetchone()[0] or []
            ms = int((time.time() - t0) * 1000)
            matches += [float(r.get("match") or 0) for r in rs]
            results.append({"no": i, "scene": scene, "query": q, "ms": ms, "routes": rs})
            print(f"\n### {i}. {scene}\n")
            print(f"景観：{' → '.join(q['sequence']) or '（なし）'}／タグ：{', '.join(q['tags']) or '-'}／"
                  f"移動：{q['travel_mode']}／結果 {len(rs)} 件／{ms} ms\n")
            if not rs:
                continue
            print("| 順 | 地点 | 移動 | 適合度 | 内訳 |")
            print("|---:|---|---|---:|---|")
            for k, r in enumerate(rs[: a.top], 1):
                spots = " → ".join(f"{s['name']}（{s['group']}）" for s in r["spots"])
                parts = r.get("score_parts") or {}
                pl = "・".join(f"{key} {float(v):.2f}" for key, v in parts.items()) or "-"
                print(f"| {k} | {spots} | {r.get('travel_mode')} {r.get('total_minutes')}分 | "
                      f"{round(float(r.get('match') or 0) * 100)}% | {pl} |")
        c.rollback()
    if matches:
        qs = statistics.quantiles(matches, n=10) if len(matches) >= 10 else []
        print(f"\n### 適合度の分布（全 {len(matches)} 件）\n")
        print(f"最小 {min(matches):.2f}／中央 {statistics.median(matches):.2f}／最大 {max(matches):.2f}"
              + (f"／10%点 {qs[0]:.2f}／90%点 {qs[-1]:.2f}" if qs else "")
              + f"／0.5以上 {sum(m >= 0.5 for m in matches)} 件")
    if a.json:
        Path(a.json).write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
