"""月次の取り込みの実行役：引数の組み立て・結果の取り出し・失敗しても続けること（DB・ネットワークなし）"""
import json
import subprocess
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools import monthly_imports as m


def test_build_argv_modes():
    s = m.STEPS["published"][0]
    assert m.build_argv(s, "dry_run")[-1] == "--dry-run" and m.build_argv(s, "apply")[-1] == "--apply"
    imp = m.STEPS["published"][2]
    assert "--apply" in m.build_argv(imp, "apply") and "--apply" not in m.build_argv(imp, "dry_run")   # 試しは既定のまま
    assert m.build_argv(imp, "apply")[-2:] == ["--json", "importance-out.json"]


def test_parse_stats_takes_last_json_line_and_file(tmp_path):
    out = 'log\n{"a": 1}\nmore log\n{"b": 2, "c": "x"}\n'
    assert m.parse_stats(out) == {"b": 2, "c": "x"}
    assert m.parse_stats("no json here") == {}
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"scored": 5, "top": [["a", 1]]}), encoding="utf-8")
    assert m.parse_stats("", str(f)) == {"scored": 5, "top": [["a", 1]]}
    assert m.compact({"scored": 5, "top": [1], "name": "x", "flag": True}) == {"scored": 5, "name": "x", "flag": True}


class P:
    def __init__(self, code, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def test_run_step_failure_and_timeout():
    s = m.Step("x", ["echo"])
    r = m.run_step(s, "apply", runner=lambda *a, **k: P(2, "", "boom\nlast line"))
    assert not r["ok"] and r["code"] == 2 and r["error"] == "last line"

    def to(*a, **k):
        raise subprocess.TimeoutExpired("x", 1)
    r = m.run_step(s, "apply", runner=to)
    assert not r["ok"] and "時間切れ" in r["error"]
    ok = m.run_step(s, "dry_run", runner=lambda *a, **k: P(0, '{"n": 3}'))
    assert ok["ok"] and ok["stats"] == {"n": 3}


def test_rescreen_only_runs_on_apply():
    r = m.run_step(m.RESCREEN, "dry_run", runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError("動かしてはいけない")))
    assert r["ok"] and r["skipped"]


def test_main_continues_after_failure_and_exits_1(monkeypatch, capsys):
    calls = []

    def fake(step, mode, runner=None):
        calls.append(step.name)
        return {"step": step.name, "ok": step.name != "bodik-facts", "skipped": False, "seconds": 1, "stats": {"n": 1},
                **({} if step.name != "bodik-facts" else {"error": "却下があります"})}
    monkeypatch.setattr(m, "run_step", fake)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert m.main(["--group", "published", "--mode", "dry_run"]) == 1
    assert calls == ["aed", "bodik-facts", "importance"]          # 失敗のあとも続く
    assert "bodik-facts の失敗：却下があります" in capsys.readouterr().out


def test_render_escapes_pipes_and_truncates():
    big = {"k": "x" * 80, "n": 1}
    rows = [{"step": "a", "ok": True, "skipped": False, "seconds": 2, "stats": {"k": "a|b"}},
            {"step": "b", "ok": True, "skipped": False, "seconds": 2, "stats": {f"key{i}": "y" * 70 for i in range(40)}}]
    r = m.render("candidates", "apply", rows)
    assert "a/b" in r and "すべて成功" in r and "…" in r
    assert big and "失敗あり" in m.render("published", "apply", [{"step": "z", "ok": False, "skipped": False, "error": "x"}])
