"""回帰テスト：承認済みの事実の取り込み（tools/facts_import.py）。試しは取り消し、弾かれた行があれば全体を取り消す"""
import json
import sys
from pathlib import Path

from osmworld import World, spot

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import facts_import  # noqa: E402


def doc(tmp_path, items):
    p = tmp_path / "f.json"
    p.write_text(json.dumps({"approved_by": "hirata", "items": items}, ensure_ascii=False), encoding="utf-8")
    return p


def test_dry_run_rolls_back_apply_commits_and_rejects_stop(db, tmp_path, monkeypatch):
    w = World(); w.point(10, 130.385, 33.585, amenity="place_of_worship", name="寺A"); w.run(db, tmp_path)
    a = spot(db, name="寺A")["id"]
    ok = {"spot_id": a, "item": "規模", "value": "351戸", "source_kind": "web", "source_url": "https://www.ur-net.go.jp/x"}
    url = db.info.dsn
    n = lambda: db.execute("select count(*) n from spot_facts").fetchone()["n"]
    r = facts_import.run(doc(tmp_path, [ok]), apply=False, url=url)
    assert (r["added"], r["committed"]) == (1, False) and n() == 0                      # 試しは取り消す
    bad = dict(ok, source_url=None)
    r = facts_import.run(doc(tmp_path, [ok, bad]), apply=True, url=url)
    assert r["committed"] is False and len(r["rejected"]) == 1 and n() == 0              # 1件でも弾かれたら全体を取り消す
    r = facts_import.run(doc(tmp_path, [ok]), apply=True, url=url)
    assert (r["added"], r["committed"]) == (1, True) and n() == 1
    r = facts_import.run(doc(tmp_path, [ok]), apply=True, url=url)
    assert (r["added"], r["skipped"]) == (0, 1) and n() == 1                            # 何度実行しても同じ
