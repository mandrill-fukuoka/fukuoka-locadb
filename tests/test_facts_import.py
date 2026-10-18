"""回帰テスト：承認済みの事実の取り込み（tools/facts_import.py）。試しは取り消し、弾かれた行があれば全体を取り消す"""
import json
import sys
from pathlib import Path

from osmworld import World, spot

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import facts_import  # noqa: E402


def doc(tmp_path, items, revoke=None):
    p = tmp_path / "f.json"
    d = {"approved_by": "hirata", "items": items}
    if revoke is not None:
        d["revoke"] = revoke
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return p


def test_dry_run_rolls_back_apply_commits_and_rejects_stop(db, tmp_path):   # 接続先は db が環境変数 DATABASE_URL に設定する
    w = World(); w.point(10, 130.385, 33.585, amenity="place_of_worship", name="寺A"); w.run(db, tmp_path)
    a = spot(db, name="寺A")["id"]
    ok = {"spot_id": a, "item": "規模", "value": "351戸", "source_kind": "web", "source_url": "https://www.ur-net.go.jp/x"}
    n = lambda: db.execute("select count(*) n from spot_facts").fetchone()["n"]
    r = facts_import.run(doc(tmp_path, [ok]), apply=False)
    assert (r["added"], r["committed"]) == (1, False) and n() == 0                      # 試しは取り消す
    bad = dict(ok, source_url=None)
    r = facts_import.run(doc(tmp_path, [ok, bad]), apply=True)
    assert r["committed"] is False and len(r["rejected"]) == 1 and n() == 0              # 1件でも弾かれたら全体を取り消す
    r = facts_import.run(doc(tmp_path, [ok]), apply=True)
    assert (r["added"], r["committed"]) == (1, True) and n() == 1
    r = facts_import.run(doc(tmp_path, [ok]), apply=True)
    assert (r["added"], r["skipped"]) == (0, 1) and n() == 1                            # 何度実行しても同じ


def test_revoke_then_import_replaces_value_and_missing_target_stops(db, tmp_path):
    w = World(); w.point(10, 130.385, 33.585, amenity="place_of_worship", name="寺B"); w.run(db, tmp_path)
    a = spot(db, name="寺B")["id"]
    old = {"spot_id": a, "item": "現況", "value": "長い値", "source_kind": "web", "source_url": "https://example.jp/x"}
    new = dict(old, value="営業中")
    facts_import.run(doc(tmp_path, [old]), apply=True)
    rv = {"spot_id": a, "item": "現況", "value": "長い値", "note": "入れ直し"}
    st = lambda: [r["status"] + ":" + r["value"] for r in db.execute("select status, value from spot_facts order by id").fetchall()]
    r = facts_import.run(doc(tmp_path, [new], [dict(rv, value="ない値")]), apply=True)
    assert r["committed"] is False and len(r["rejected"]) == 1 and st() == ["approved:長い値"]   # 対象がなければ全体を取り消す
    r = facts_import.run(doc(tmp_path, [new], [rv]), apply=True)
    assert (r["revoked"], r["added"], r["committed"]) == (1, 1, True)
    assert st() == ["revoked:長い値", "approved:営業中"]
