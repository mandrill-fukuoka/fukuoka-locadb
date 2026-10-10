"""一括実行：OSMの取得 → 地点の反映 → 住所 → 流れの生成 → AI分類 → 変化リスク監視

  python -m locadb.pipeline --dry-run                 # 試し：本番を変えずに差分レポートだけ出す
  python -m locadb.pipeline --apply                   # 本番に反映
  python -m locadb.pipeline --apply --pbf fukuoka.osm.pbf   # 手元のファイルを使う（テスト用）

--apply でも、次のときは反映せずに止める（レポートは残る）
  - 1回で対象外になる地点が多すぎる（地点の5%超、かつ50件超）
  - 地点の総数が前回より2割以上減る
  止めた内容を確認して問題がなければ --allow-large-change を付けて実行する
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from . import address, build_clusters, monitor_risk, osm_source
from .categories import RULES_VERSION
from .db import code_version, connect
from .sync import MAX_ARCHIVE_RATIO, Sync


def get_snapshot(pbf: str | None, watch: set[str]):
    if pbf:
        src = {"file": pbf}
        path = Path(pbf)
    else:
        tmp = Path(tempfile.mkdtemp())
        full = tmp / "kyushu.osm.pbf"
        print("[1] OSMファイルの取得（Geofabrik・九州）")
        src = osm_source.download(full)
        path = tmp / "fukuoka.osm.pbf"
        osm_source.clip(full, path)
        full.unlink()
    src["osm_timestamp"] = osm_source.file_timestamp(path)
    print(f"    データ時点 {src.get('osm_timestamp')}")
    print("[2] 読み取り")
    snap = osm_source.read(path, watch)
    print("    " + json.dumps(snap.meta["by_category"], ensure_ascii=False))
    return snap, src


def run_sync(conn, snap_loader, mode: str, allow_large_change: bool = False) -> dict:
    """取得→反映→判定→（試し・停止なら取り消し）。結果 {run_id, status, summary, report}"""
    run_id = conn.execute("""insert into data_runs (mode, code_version, rules_version) values (%s,%s,%s)
                             returning id""", (mode, code_version(), RULES_VERSION)).fetchone()["id"]
    conn.commit()
    try:
        watch = {r["key"] for r in conn.execute("select key from features where kind='element'")}
        snap, src = snap_loader(watch)
        print(f"[3] 地点の反映（{'本番' if mode == 'apply' else '試し'}）")
        sync = Sync(conn, snap, run_id, allow_large_change=allow_large_change)
        summary = sync.run_all(snap.present)
        report = sync.report(summary)

        stop = []
        live = max(summary["spots_before"], 1)
        if summary["archived"] > 50 and summary["archived"] > live * MAX_ARCHIVE_RATIO:
            stop.append(f"対象外にする地点が多すぎます（{summary['archived']}件）")
        if summary["spots_before"] >= 100 and summary["spots_after"] < summary["spots_before"] * 0.8:
            stop.append(f"地点の総数が大きく減ります（{summary['spots_before']} → {summary['spots_after']}）")
        status = "ok"
        if stop and mode == "apply" and not allow_large_change:
            report = "## ！反映を止めました\n" + "\n".join(f"- {x}" for x in stop) + \
                     "\n- 内容を確認し、問題がなければ allow_large_change を付けて再実行してください\n\n" + report
            status = "stopped"
        counts = snap.meta["by_category"]

        if mode == "dry_run" or status == "stopped":
            conn.rollback()
            run_id = conn.execute(
                """insert into data_runs (mode, status, finished_at, source, code_version, rules_version,
                                          counts, summary, report)
                   values (%s,%s,now(),%s,%s,%s,%s,%s,%s) returning id""",
                (mode, status, json.dumps(src), code_version(), RULES_VERSION, json.dumps(counts),
                 json.dumps(summary, ensure_ascii=False), report)).fetchone()["id"]
            conn.commit()
            print(report)
            print(f"\n（{'試し実行' if mode == 'dry_run' else '停止'}のため、データは変更していません。記録 #{run_id}）")
            return {"run_id": run_id, "status": status, "summary": summary, "report": report}

        conn.execute("""update data_runs set status='ok', finished_at=now(), source=%s, counts=%s, summary=%s,
                        report=%s where id=%s""",
                     (json.dumps(src), json.dumps(counts), json.dumps(summary, ensure_ascii=False), report, run_id))
        conn.commit()
        print(report)
        return {"run_id": run_id, "status": status, "summary": summary, "report": report}
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        conn.execute("update data_runs set status='failed', finished_at=now(), error=%s where id=%s",
                     (repr(e), run_id))
        conn.commit()
        print(f"！失敗しました（データは変更していません）: {e}")
        raise


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--pbf")
    ap.add_argument("--allow-large-change", action="store_true")
    ap.add_argument("--skip-address", action="store_true")
    ap.add_argument("--skip-extras", action="store_true", help="流れの生成・AI分類・変化リスク監視を省く")
    a = ap.parse_args()
    mode = "apply" if a.apply else "dry_run"

    with connect() as conn:
        res = run_sync(conn, lambda watch: get_snapshot(a.pbf, watch), mode, a.allow_large_change)
    if res["status"] == "stopped":
        return 3
    if mode == "dry_run":
        return 0
    run_id = res["run_id"]
    if not a.skip_address:
        print("[4] 住所（国土地理院）")
        address.run(run_id)
    if not a.skip_extras:
        print("[5] 流れの自動生成")
        build_clusters.run_all(run_id)
        if os.environ.get("ANTHROPIC_API_KEY"):
            from . import classify
            print("[6] AI分類")
            classify.run_all(run_id)
        else:
            print("[6] AI分類：スキップ（ANTHROPIC_API_KEY 未設定）")
            with connect() as conn:
                conn.execute("insert into job_runs (run_id, job, finished_at, stats) values (%s,'classify',now(),%s)",
                             (run_id, json.dumps({"skipped": "ANTHROPIC_API_KEY 未設定"}, ensure_ascii=False)))
        print("[7] 変化リスク監視")
        monitor_risk.run(run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
