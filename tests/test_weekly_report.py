"""週次の実行結果のまとめ：集計と、要対応の判定（ネットワーク・DBなし）"""
from collections import Counter
from datetime import datetime, timedelta, timezone

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
import weekly_report as w

NOW = datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)


def run(name, conclusion, status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion}


def test_summarize_and_problems_flag_cancelled_verify():
    counts = w.summarize_runs([run("verify", "cancelled"), run("pipeline", "success"), run("ci", "failure"),
                               run("keepalive", None, "in_progress")])
    assert counts["verify"]["cancelled"] == 1 and counts["keepalive"]["running"] == 1
    ok = {"pipeline": NOW - timedelta(days=1), "verify": NOW - timedelta(days=1)}
    p = w.problems(counts, ok, NOW)
    assert any(x.startswith("verify：失敗・中断 1") for x in p)
    assert not any(x.startswith("ci") for x in p)                      # ci の失敗は PR の途中経過


def test_stale_last_success_is_flagged():
    ok = {"pipeline": NOW - timedelta(days=1), "verify": NOW - timedelta(days=16)}
    p = w.problems({}, ok, NOW)
    assert p == ["verify：最後の成功が 2026-11-01 15:00（JST）で、16 日前です"] or any("16 日前" in x for x in p)
    assert w.problems({}, {"pipeline": None, "verify": NOW}, NOW) == ["pipeline：DBに成功の記録がありません"]


def test_render_title_marks_state():
    title, body = w.render(NOW, {}, {"pipeline": NOW, "verify": None}, {"掲載中の地点": 10717}, ["verify：x"])
    assert "要対応あり" in title and "## 要対応" in body and "10,717" in body
    title, _ = w.render(NOW, {}, {"pipeline": NOW}, {}, [])
    assert "異常なし" in title
