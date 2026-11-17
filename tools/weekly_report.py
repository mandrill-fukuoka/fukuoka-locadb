"""週次の実行結果のまとめ：直近1週間の Actions の成否と、DBに残った最後の成功の日時を1つにまとめ、
GitHub の Issue に出す（管理者に通知が届く）。止まっていても気づけない状態をなくすためのもの（2026-11-17）。

使い方：python tools/weekly_report.py [--no-issue]
環境変数：GITHUB_TOKEN・GITHUB_REPOSITORY（Actions が自動で入れる）、DATABASE_URL（読み取りのみ）
公開リポジトリなので、書くのは件数と日時だけ（キー・個人情報・本文は出さない）。
"""
import argparse
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
# 毎週動くはずの処理と、最後の成功がこの日数より古ければ「止まっている疑い」とする日数
EXPECTED_WEEKLY = {"pipeline": 9, "verify": 9}
WATCHED = ["pipeline", "verify", "keepalive", "ci", "deploy-web", "db-migrate", "yardstick"]


def summarize_runs(runs: list[dict]) -> dict[str, Counter]:
    """ワークフロー名ごとに、完了した実行の結果（success / failure / cancelled など）を数える"""
    out: dict[str, Counter] = defaultdict(Counter)
    for r in runs:
        if r.get("status") != "completed":
            out[r["name"]]["running"] += 1
        else:
            out[r["name"]][r.get("conclusion") or "unknown"] += 1
    return out


def problems(counts: dict[str, Counter], last_ok: dict[str, datetime | None], now: datetime) -> list[str]:
    """要対応の項目。失敗・キャンセル（時間切れを含む）があった処理と、毎週動くはずなのに最後の成功が古い処理"""
    out = []
    for name in WATCHED:
        c = counts.get(name, Counter())
        bad = c.get("failure", 0) + c.get("cancelled", 0) + c.get("timed_out", 0)
        if bad and name != "ci":                       # ci の失敗は PR の途中経過なので数えない
            out.append(f"{name}：失敗・中断 {bad} 件（直近1週間）")
    for name, days in EXPECTED_WEEKLY.items():
        t = last_ok.get(name)
        if t is None:
            out.append(f"{name}：DBに成功の記録がありません")
        elif now - t > timedelta(days=days):
            out.append(f"{name}：最後の成功が {t.astimezone(JST):%Y-%m-%d %H:%M}（JST）で、{(now - t).days} 日前です")
    return out


def render(now: datetime, counts, last_ok, db: dict, probs: list[str]) -> tuple[str, str]:
    mark = "要対応あり" if probs else "異常なし"
    title = f"週次の実行結果 {now.astimezone(JST):%Y-%m-%d}（{mark}）"
    L = [f"集計：{now.astimezone(JST):%Y-%m-%d %H:%M}（JST）時点の直近7日間", ""]
    if probs:
        L += ["## 要対応", ""] + [f"- {p}" for p in probs] + [""]
    L += ["## Actions の実行結果（直近7日）", "", "| 処理 | 成功 | 失敗 | 中断 | 実行中 |", "|---|---:|---:|---:|---:|"]
    for name in WATCHED:
        c = counts.get(name, Counter())
        L.append(f"| {name} | {c.get('success', 0)} | {c.get('failure', 0)} | "
                 f"{c.get('cancelled', 0) + c.get('timed_out', 0)} | {c.get('running', 0)} |")
    L += ["", "## DBに残っている最後の成功（JST）", ""]
    for name, t in last_ok.items():
        L.append(f"- {name}：{t.astimezone(JST):%Y-%m-%d %H:%M}" if t else f"- {name}：記録なし")
    if db:
        L += ["", "## データの件数", ""] + [f"- {k}：{v:,}" for k, v in db.items()]
    L += ["", "（DBの呼び出し回数は Supabase の画面で確認する。ここでは取れない）"]
    return title, "\n".join(L) + "\n"


def fetch_runs(repo: str, token: str, since: datetime) -> list[dict]:
    import requests
    runs, page = [], 1
    while page <= 5:
        r = requests.get(f"https://api.github.com/repos/{repo}/actions/runs",
                         params={"created": f">={since:%Y-%m-%d}", "per_page": 100, "page": page},
                         headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}, timeout=30)
        r.raise_for_status()
        got = r.json().get("workflow_runs", [])
        runs += got
        if len(got) < 100:
            break
        page += 1
    return [r for r in runs if datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) >= since]


def db_facts() -> tuple[dict, dict]:
    import psycopg
    last_ok, db = {}, {}
    with psycopg.connect(os.environ["DATABASE_URL"]) as c:
        c.read_only = True
        r = c.execute("select max(started_at) from data_runs where mode = 'apply' and status = 'ok'").fetchone()
        last_ok["pipeline"] = r[0]
        r = c.execute("select max(finished_at) from job_runs where job = 'verify' and error is null "
                      "and finished_at is not null").fetchone()
        last_ok["verify"] = r[0]
        db["掲載中の地点"] = c.execute("select count(*) from spots where data_status <> 'archived'").fetchone()[0]
        db["要確認の地点"] = c.execute("select count(*) from spots where data_status = 'needs_review'").fetchone()[0]
        db["自動チェックの未対応の指摘"] = c.execute("select count(*) from spot_checks where status = 'open'").fetchone()[0]
        db["要確認の変化リスク"] = c.execute("select count(*) from change_risks where verified = false and risk_type <> 'none'").fetchone()[0]
    return last_ok, db


def post_issue(repo: str, token: str, title: str, body: str) -> str:
    import requests
    r = requests.post(f"https://api.github.com/repos/{repo}/issues", json={"title": title, "body": body},
                      headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}, timeout=30)
    r.raise_for_status()
    return r.json()["html_url"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-issue", action="store_true", help="Issue を作らず、標準出力にだけ出す")
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    repo, token = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    counts = summarize_runs(fetch_runs(repo, token, now - timedelta(days=7)))
    last_ok, db = db_facts()
    title, body = render(now, counts, last_ok, db, problems(counts, last_ok, now))
    print(title + "\n\n" + body)
    if not a.no_issue:
        print("Issue:", post_issue(repo, token, title, body))


if __name__ == "__main__":
    sys.exit(main())
