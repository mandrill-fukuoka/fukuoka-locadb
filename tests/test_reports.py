"""誤りの連絡口（順5、DB変更280）"""
import pytest
from test_routes import add


def test_submit_list_handle(db):
    a = add(db, "連絡の地点", "coast", 0, 0)
    rid = db.execute("select report_submit(%s, 'wrong_place', '  位置が50mずれています  ') as r", (a,)).fetchone()["r"]
    db.commit()
    rows = db.execute("select reports_list('open') as r").fetchone()["r"]
    assert [(r["id"], r["spot_name"], r["kind"], r["note"], r["status"]) for r in rows] == [(rid, "連絡の地点", "wrong_place", "位置が50mずれています", "open")]
    db.execute("select report_handle(%s, 'done', 'tester', '直した')", (rid,))
    db.commit()
    assert db.execute("select reports_list('open') as r").fetchone()["r"] == []
    done = db.execute("select reports_list('done') as r").fetchone()["r"][0]
    assert (done["handled_by"], done["handled_note"]) == ("tester", "直した")
    db.execute("select report_handle(%s, 'open', 'tester')", (rid,))                    # 戻せる
    db.commit()
    assert len(db.execute("select reports_list('all') as r").fetchone()["r"]) == 1
    assert db.execute("select reports_list('open') as r").fetchone()["r"][0]["handled_by"] is None


def test_submit_without_spot_and_validation(db):
    db.execute("select report_submit(null, 'other', 'サイト全体への連絡')")
    db.commit()
    assert db.execute("select reports_list('open') as r").fetchone()["r"][0]["spot_id"] is None
    for args in ((None, "bad", "x"), (None, "other", "   "), (999999, "other", "x")):
        with pytest.raises(Exception):
            db.execute("select report_submit(%s, %s, %s)", args)
        db.rollback()


def test_report_survives_spot_removal(db):
    a = add(db, "消える地点", "coast", 0, 0)
    db.execute("select report_submit(%s, 'closed', 'なくなりました')", (a,))
    db.execute("delete from spots where id = %s", (a,))
    db.commit()
    r = db.execute("select reports_list('all') as r").fetchone()["r"][0]
    assert (r["spot_id"], r["spot_name"]) == (None, "消える地点")
