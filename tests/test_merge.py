"""回帰テスト：重複の疑いの統合（merge_spots）"""
from osmworld import World, spot


def test_merge_same_name(db, tmp_path):
    """同じ名前の2点をまとめられる（別名が空になっても失敗しない。2026-10-24 本番で 400 になっていた）"""
    w = World()
    w.point(1, 130.40, 33.59, railway="station", name="薬院駅")
    w.point(2, 130.4009, 33.5905, railway="station", name="薬院駅")
    w.run(db, tmp_path)
    pair = db.execute("select duplicate_candidates() r").fetchone()["r"]
    assert len(pair) == 1
    keep, drop = pair[0]["a_id"], pair[0]["b_id"]
    db.execute("select merge_spots(%s, %s, 'tester')", (keep, drop))
    db.commit()
    assert spot(db, id=drop)["data_status"] == "archived"
    assert spot(db, id=keep)["alt_names"] == []
    assert db.execute("select duplicate_candidates() r").fetchone()["r"] == []
