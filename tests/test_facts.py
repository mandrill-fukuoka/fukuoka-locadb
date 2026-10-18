"""回帰テスト：事実カード（DB変更180、sql/api/70_facts.sql）

人が承認した事実だけを持つ。出典のない事実は入らず、取り消しても行は残る。公開の画面は facts_for_spot で承認済みだけを読む
"""
import json

import psycopg
import pytest

from osmworld import World, spot


def rpc(db, sql, *args):
    r = db.execute(f"select {sql} r", args).fetchone()["r"]
    db.commit()
    return r


def imp(db, items, user="hirata"):
    return rpc(db, "facts_import(%s::jsonb, %s)", json.dumps(items, ensure_ascii=False), user)


WEB = {"item": "規模", "value": "351戸", "note": "戸数", "source_kind": "web", "source_url": "https://www.ur-net.go.jp/x"}
WIKI = {"item": "人口", "value": "353人", "as_of": "令和2年国勢調査", "source_kind": "wikipedia",
        "source_url": "https://ja.wikipedia.org/wiki/x", "source_title": "玄界島 - Wikipedia", "license": "CC BY-SA 4.0"}


@pytest.fixture
def two(db, tmp_path):
    w = World()
    w.point(10, 130.385, 33.585, amenity="place_of_worship", name="寺A")
    w.point(11, 130.390, 33.586, amenity="place_of_worship", name="寺B")
    w.run(db, tmp_path)
    return spot(db, name="寺A")["id"], spot(db, name="寺B")["id"]


def test_import_adds_and_skips_duplicates(db, two):
    a, b = two
    r = imp(db, [dict(WEB, spot_id=a), dict(WEB, spot_id=a), dict(WIKI, spot_id=a)])
    assert (r["added"], r["skipped"], r["rejected"]) == (2, 1, [])
    f = rpc(db, "facts_for_spot(%s)", a)
    assert [(x["item"], x["value"]) for x in f] == [("規模", "351戸"), ("人口", "353人")]   # 項目の順に並ぶ
    assert f[1]["as_of"] == "令和2年国勢調査" and f[1]["license"] == "CC BY-SA 4.0"
    assert all("quote" not in x for x in f)                                                  # 原文は公開用に含めない
    assert [h["event"] for h in db.execute("select event from spot_history where spot_id=%s order by id", (a,)).fetchall()] == ["fact_added"] * 2


def test_validation_rejects_without_source_or_vocabulary(db, two):
    a, b = two
    r = imp(db, [
        dict(WEB, spot_id=a, source_url=None),                                   # 出典なし
        dict(WIKI, spot_id=a, license=None),                                     # Wikipedia でライセンスなし
        dict(WEB, spot_id=a, item="色", value="赤"),                              # 項目が語彙にない
        dict(WEB, spot_id=a, value="  "),                                        # 値が空
        dict(WEB, spot_id=999999),                                               # 地点がない
        {"spot_id": a, "item": "現況", "value": "稼働中", "source_kind": "manual"},  # 手入力は出典なしでよい
    ])
    assert r["added"] == 1 and len(r["rejected"]) == 5
    assert {x["reason"] for x in r["rejected"]} == {"出典URL（https）がない", "Wikipedia は記事名とライセンスが必要", "項目が語彙にない",
                                                    "値が空、または200字を超えている", "地点がない、または対象外"}


def test_archived_spot_is_rejected_and_table_enforces_source(db, two):
    a, b = two
    db.execute("update spots set data_status='archived', archived_reason='manual:test' where id=%s", (b,)); db.commit()
    assert imp(db, [dict(WEB, spot_id=b)])["rejected"][0]["reason"] == "地点がない、または対象外"
    with pytest.raises(psycopg.errors.CheckViolation):                           # 関数を通さない直接の書き込みも、出典なしは入らない
        db.execute("insert into spot_facts (spot_id,item,value,source_kind,approved_by) values (%s,'規模','1戸','web','x')", (a,))
    db.rollback()


def test_revoke_hides_but_keeps_row_and_allows_reimport(db, two):
    a, b = two
    imp(db, [dict(WEB, spot_id=a)])
    fid = rpc(db, "facts_for_spot(%s)", a)[0]["id"]
    assert rpc(db, "fact_revoke(%s, %s, %s)", fid, "hirata", "誤り")["status"] == "revoked"
    assert rpc(db, "facts_for_spot(%s)", a) == []
    assert db.execute("select status, revoked_by, revoke_note from spot_facts where id=%s", (fid,)).fetchone() == \
        {"status": "revoked", "revoked_by": "hirata", "revoke_note": "誤り"}
    with pytest.raises(psycopg.errors.RaiseException):
        rpc(db, "fact_revoke(%s, %s)", fid, "hirata")                             # 二重の取り消しは弾く
    db.rollback()
    assert imp(db, [dict(WEB, spot_id=a)])["added"] == 1                          # 取り消した事実と同じ内容を入れ直せる
    assert rpc(db, "facts_counts()")["spots"] == 1


def test_import_requires_user_and_limit(db, two):
    a, b = two
    with pytest.raises(psycopg.errors.RaiseException):
        imp(db, [dict(WEB, spot_id=a)], user="")
    db.rollback()
    with pytest.raises(psycopg.errors.RaiseException):
        imp(db, [dict(WEB, spot_id=a, value=f"{i}戸") for i in range(201)])
    db.rollback()
