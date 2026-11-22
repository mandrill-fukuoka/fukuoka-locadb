"""月次の取り込み（順6・作業方針 8章）：公開データの取り込みと、候補の収集を決まった順に回し、結果を1つにまとめる。

  python -m tools.monthly_imports --group published|candidates --mode dry_run|apply [--rescreen]

  published  公開データそのもの：AED（BODIK）→ 事実カード（BODIK）→ 重要度（Wikidata・閲覧数）
  candidates 候補の収集（公開しない。確認画面に出る）：Commons 写真 → Mapillary 写真 → 追加候補 → BODIK 追加候補
             --rescreen を付けると、最後に未決の写真候補の重複を整える（photo_rescreen apply --yes。reset で戻せる）

- 1つの工程が失敗しても残りは続ける。最後に失敗した工程があれば終了コードを 1 にする（Actions が赤くなり、週次の Issue にも出る）。
- 各工程は自分の安全装置（件数が半分未満に減ったら止まる・却下があれば止まる、など）を持つ。ここでは迂回しない。
- apply のときだけ job_runs に「monthly-imports-<group>」を残す（週次レポートが最後の成功を見る）。dry_run は残さない。
- 画面（GITHUB_STEP_SUMMARY）に書くのは件数と日時だけ。キー・本文・個人情報は出さない。
"""
import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field

PY = sys.executable


@dataclass
class Step:
    name: str
    argv: list[str]                       # {mode} は --dry-run / --apply に置き換える
    json_file: str | None = None          # 結果を標準出力でなくファイルに書く工程
    timeout_min: int = 90
    only_apply: bool = False              # apply のときだけ動かす（試し実行の仕組みがない工程）
    extra: dict = field(default_factory=dict)


STEPS: dict[str, list[Step]] = {
    "published": [
        Step("aed", [PY, "-m", "locadb.bodik_aed", "{mode}"], timeout_min=30),
        Step("bodik-facts", [PY, "-m", "locadb.bodik_facts", "{mode}"], timeout_min=45),
        Step("importance", [PY, "tools/importance_import.py", "run", "{apply}", "--json", "importance-out.json"],
             json_file="importance-out.json", timeout_min=45),
    ],
    "candidates": [
        Step("commons-photos", [PY, "-m", "locadb.commons_photos", "{mode}", "--limit", "200"], timeout_min=120),
        Step("mapillary-photos", [PY, "-m", "locadb.mapillary_photos", "{mode}", "--limit", "200"], timeout_min=120),
        Step("addition-candidates", [PY, "-m", "locadb.addition_candidates", "{mode}"], timeout_min=60),
        Step("bodik-import", [PY, "-m", "locadb.bodik_import", "{mode}"], timeout_min=30),
    ],
}
RESCREEN = Step("photo-rescreen", [PY, "-m", "tools.photo_rescreen", "apply", "--yes"], timeout_min=30, only_apply=True)

# 画面に出す統計の最大文字数（公開ログなので長くしない）
MAX_STATS_CHARS = 1500


def build_argv(step: Step, mode: str) -> list[str]:
    flag = "--apply" if mode == "apply" else "--dry-run"
    out = []
    for a in step.argv:
        if a == "{mode}":
            out.append(flag)
        elif a == "{apply}":
            if mode == "apply":
                out.append("--apply")          # importance_import は試し実行が既定で、--apply で反映する
        else:
            out.append(a)
    return out


def parse_stats(stdout: str, json_file: str | None = None) -> dict:
    """工程の結果（JSON）を取り出す。ファイルがあればそれを、なければ標準出力の最後の JSON の行を使う。
    取れなければ空（工程の成否は終了コードで決める）。"""
    if json_file and os.path.exists(json_file):
        try:
            with open(json_file, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            pass
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict):
                return d
    return {}


def compact(stats: dict) -> dict:
    """数値・短い文字列だけを残す（表・長い一覧は捨てる）。"""
    out = {}
    for k, v in stats.items():
        if isinstance(v, bool) or isinstance(v, (int, float)) or (isinstance(v, str) and len(v) <= 80):
            out[k] = v
    return out


def run_step(step: Step, mode: str, runner=subprocess.run) -> dict:
    argv = build_argv(step, mode)
    t0 = time.time()
    res = {"step": step.name, "ok": False, "skipped": False}
    if step.only_apply and mode != "apply":
        res.update(ok=True, skipped=True)
        return res
    try:
        p = runner(argv, capture_output=True, text=True, timeout=step.timeout_min * 60)
        sys.stdout.write(p.stdout or "")
        sys.stderr.write((p.stderr or "")[-4000:])
        res["ok"] = p.returncode == 0
        res["code"] = p.returncode
        res["stats"] = compact(parse_stats(p.stdout or "", step.json_file))
        if p.returncode != 0:
            tail = (p.stderr or p.stdout or "").strip().splitlines()[-1:] or [""]
            res["error"] = tail[0][:200]
    except subprocess.TimeoutExpired:
        res.update(code=-1, error=f"時間切れ（{step.timeout_min} 分）", stats={})
    res["seconds"] = round(time.time() - t0)
    return res


def render(group: str, mode: str, results: list[dict]) -> str:
    bad = [r for r in results if not r["ok"]]
    L = [f"## 月次の取り込み：{group}（{mode}）— " + ("失敗あり" if bad else "すべて成功"), "",
         "| 工程 | 結果 | 所要（秒） | 主な件数 |", "|---|---|---:|---|"]
    for r in results:
        st = "対象外" if r["skipped"] else ("成功" if r["ok"] else "失敗")
        s = json.dumps(r.get("stats") or {}, ensure_ascii=False)
        s = s if len(s) <= MAX_STATS_CHARS else s[:MAX_STATS_CHARS] + "…"
        L.append(f"| {r['step']} | {st} | {r.get('seconds', 0)} | {s.replace('|', '/')} |")
    for r in bad:
        L.append(f"\n- {r['step']} の失敗：{r.get('error', '')}")
    return "\n".join(L) + "\n"


def record(group: str, results: list[dict]) -> None:
    """apply のとき job_runs に残す。失敗した工程があれば error を入れる（最後の成功の判定から外れる）。"""
    from locadb.db import connect, job
    bad = [r["step"] for r in results if not r["ok"]]
    with connect() as conn:
        try:
            with job(conn, f"monthly-imports-{group}") as stats:
                stats["steps"] = {r["step"]: {"ok": r["ok"], "seconds": r.get("seconds", 0), **(r.get("stats") or {})}
                                  for r in results}
                if bad:
                    raise RuntimeError("失敗した工程：" + "、".join(bad))
        except RuntimeError:
            pass                                # 失敗は呼び出し側が終了コードで知らせる


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--group", choices=sorted(STEPS), required=True)
    ap.add_argument("--mode", choices=["dry_run", "apply"], default="dry_run")
    ap.add_argument("--rescreen", action="store_true", help="candidates の最後に写真候補の重複を整える（apply のときだけ）")
    a = ap.parse_args(argv)
    steps = list(STEPS[a.group]) + ([RESCREEN] if a.rescreen and a.group == "candidates" else [])
    results = [run_step(s, a.mode) for s in steps]
    text = render(a.group, a.mode, results)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)
    if a.mode == "apply" and os.environ.get("DATABASE_URL"):
        record(a.group, results)
    return 1 if any(not r["ok"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
