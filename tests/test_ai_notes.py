"""回帰テスト：AIの下書き（DB変更130、sql/api/50_ai.sql）

AI（LAYA・claude）は ai_submit で下書きとタグだけを書き、公開の説明文（spots.summary）は人が ai_review で採用したときだけ入る
"""
import json

import psycopg
import pytest

from osmworld import World, spot


def rpc(db, sql, *args):
    r = db.execute(f"select {sql} r", args).fetchone()["r"]
    db.commit()
    return r


@pytest.fixture
def two(db, tmp_path):
    w = World()
    w.point(10, 130.385, 33.585, amenity="place_of_worship", name="寺A")
    w.point(11, 130.390, 33.586, amenity="place_of_worship", name="寺B")
    w.run(db, tmp_path)
    return spot(db, name="寺A")["id"], spot(db, name="寺B")["id"], w


def submit(db, source, items, model="qwen2.5:14b", pv="laya-v1"):
    return rpc(db, "ai_submit(%s, %s::jsonb, %s, %s)", source, json.dumps(items, ensure_ascii=False), model, pv)


def drafts(db, sid):
    return db.execute("select * from spot_ai_notes where spot_id=%s order by id", (sid,)).fetchall()


def test_submit_creates_draft_not_public(db, two):
    a, b, _ = two
    r = submit(db, "laya", [{"spot_id": a, "summary": "石畳の参道が続く寺。", "tags": [{"code": "nostalgic", "score": 0.8}]}])
    assert r["summaries"] == 1 and r["tags"] == 1 and r["rejected"] == []
    assert spot(db, id=a)["summary"] is None                                   # 公開の値は変わらない
    d = drafts(db, a)
    assert [(x["source"], x["status"], x["model"], x["prompt_version"]) for x in d] == [("laya", "draft", "qwen2.5:14b", "laya-v1")]
    t = db.execute("select origin, source, score from spot_tags where spot_id=%s", (a,)).fetchone()
    assert t["origin"] == "ai" and t["source"] == "laya" and t["score"] == pytest.approx(0.8)


def test_resubmit_supersedes_and_validation(db, two):
    a, b, _ = two
    submit(db, "laya", [{"spot_id": a, "summary": "一つ目"}])
    db.execute("update spots set data_status='archived', archived_reason='manual:test' where id=%s", (b,)); db.commit()
    r = submit(db, "laya", [{"spot_id": a, "summary": "二つ目", "tags": [{"code": "no_such_tag"}]},
                            {"spot_id": b, "summary": "対象外の地点"},
                            {"spot_id": 999999, "summary": "ない地点"},
                            {"spot_id": a, "summary": "長" * 801}])
    assert r["summaries"] == 1
    reasons = sorted(x["reason"][:6] for x in r["rejected"])
    assert len(reasons) == 4 and any("語彙にないタグ" in x["reason"] for x in r["rejected"])
    assert [(x["body"], x["status"]) for x in drafts(db, a)] == [("一つ目", "superseded"), ("二つ目", "draft")]
    with pytest.raises(psycopg.errors.RaiseException):
        submit(db, "laya", [{"spot_id": a}] * 201)
    db.rollback()
    with pytest.raises(psycopg.errors.RaiseException):
        submit(db, "gpt", [{"spot_id": a, "summary": "x"}])
    db.rollback()


def test_human_tags_are_never_overwritten(db, two):
    a, _, _ = two
    db.execute("select review_spot(%s, 'scouted', 'field_checked', null, array['nostalgic'], 'hirata')", (a,)); db.commit()
    submit(db, "laya", [{"spot_id": a, "tags": [{"code": "nostalgic", "score": 0.1}, {"code": "lively", "score": 0.6}]}])
    rows = {r["tag_code"]: r for r in db.execute("select * from spot_tags where spot_id=%s", (a,)).fetchall()}
    assert rows["nostalgic"]["origin"] == "human" and rows["nostalgic"]["score"] == 1.0
    assert rows["lively"]["origin"] == "ai" and rows["lively"]["source"] == "laya"
    submit(db, "laya", [{"spot_id": a, "tags": []}])                           # 同じ出どころのAIタグは置き換え
    assert {r["tag_code"] for r in db.execute("select tag_code from spot_tags where spot_id=%s", (a,))} == {"nostalgic"}


def test_accept_edit_and_reject(db, two):
    a, b, _ = two
    submit(db, "laya", [{"spot_id": a, "summary": "LAYAの案"}, {"spot_id": b, "summary": "使わない案"}])
    submit(db, "claude", [{"spot_id": a, "summary": "Claudeの案"}])
    na = [x for x in drafts(db, a) if x["source"] == "laya"][0]["id"]
    rpc(db, "ai_review(%s, 'accept', 'hirata', %s)", na, "LAYAの案を直したもの")
    s = spot(db, id=a)
    assert s["summary"] == "LAYAの案を直したもの" and s["summary_by"] == "hirata"
    st = {x["source"]: (x["status"], x["review_note"]) for x in drafts(db, a)}
    assert st == {"laya": ("accepted", "直して採用"), "claude": ("superseded", None)}
    nb = drafts(db, b)[0]["id"]
    rpc(db, "ai_review(%s, 'reject', 'hirata')", nb)
    assert spot(db, id=b)["summary"] is None and drafts(db, b)[0]["status"] == "rejected"
    ev = [r["event"] for r in db.execute("select event from spot_history where spot_id in (%s,%s) order by id", (a, b))]
    assert "summary_accepted" in ev and "summary_rejected" in ev
    with pytest.raises(psycopg.errors.RaiseException):                         # 処理済みはもう一度処理できない
        rpc(db, "ai_review(%s, 'accept', 'hirata')", nb)
    db.rollback()
    # 公開画面の検索にも採用した説明文が出る
    hits = rpc(db, "search_spots(p_q => '寺A')")
    assert hits[0]["summary"] == "LAYAの案を直したもの"


def test_summary_requires_author(db, two):
    a, _, _ = two
    with pytest.raises(psycopg.errors.CheckViolation):                         # AIが直接書こうとしても入らない
        db.execute("update spots set summary='直接' where id=%s", (a,))
    db.rollback()
    db.execute("select review_spot(%s, 'candidate', 'desk_checked', '人が書いた説明', null, 'hirata')", (a,)); db.commit()
    s = spot(db, id=a)
    assert s["summary"] == "人が書いた説明" and s["summary_by"] == "hirata"
    r = rpc(db, "create_manual_spot('手入力の寺', 33.59, 130.395, 'shrine_temple', p_summary => '説明', p_user => 'hirata')")
    assert spot(db, id=r["id"])["summary_by"] == "hirata"
    r2 = rpc(db, "create_manual_spot('説明なし', 33.59, 130.396, 'shrine_temple', p_user => 'hirata')")
    assert spot(db, id=r2["id"])["summary_by"] is None


def test_view_detail_drafts_list_and_diag(db, two):
    a, b, _ = two
    submit(db, "laya", [{"spot_id": a, "summary": "下書き"}])
    v = {r["id"]: r for r in db.execute("select id, laya_draft, summary, laya_last_at from v_fukuoka_spots")}
    assert v[a]["laya_draft"] is True and v[b]["laya_draft"] is False and v[a]["laya_last_at"] is not None
    pub = rpc(db, "spot_detail(%s)", a)
    assert pub["drafts"] is None and pub["summary_by"] is None                 # ログインなしでは下書きを返さない
    adm = rpc(db, "spot_detail(%s, true)", a)
    assert [d["body"] for d in adm["drafts"]] == ["下書き"]
    lst = rpc(db, "ai_drafts()")
    assert [(x["spot_id"], x["source"]) for x in lst] == [(a, "laya")]
    assert rpc(db, "diag_summary()")["ai_notes"] == {"laya:draft": 1}
