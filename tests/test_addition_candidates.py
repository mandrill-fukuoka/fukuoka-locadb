"""回帰テスト：追加候補（Wikipedia・Commons。DB変更203）。外部へは出ない（点は手で作る）"""
import json

import pytest

from locadb import addition_candidates as ac
from locadb.categories import CATEGORIES
from osmworld import World, spots


def rpc(db, fn, **kw):
    args = ", ".join(f"{k} => %({k})s" for k in kw)
    row = db.execute(f"select {fn}({args}) r", kw).fetchone()
    db.commit()
    return row["r"]


def test_title_cleanup_admin_and_category_hints():
    assert ac.clean_title("雷神社 (糸島市)") == "雷神社" and ac.clean_title("天神（福岡市）") == "天神"
    assert all(ac.is_admin_title(x) for x in ["志免町", "博多区", "福岡市", "大野城市", "天神一丁目", "筑紫平野"])
    assert not any(ac.is_admin_title(x) for x in ["博多駅", "警固神社", "大濠公園"])
    assert ac.suggest_category("土井駅") == "station" and ac.suggest_category("警固神社") == "shrine_temple"
    assert ac.suggest_category("福岡市立博多小学校") == "school" and ac.suggest_category("大濠公園") == "park"
    assert ac.suggest_category("須玖遺跡群") is None            # 当たらなければ空欄（人が選ぶ）
    assert {c for _, c in ac.CATEGORY_HINTS} <= set(CATEGORIES)    # 種別の案は実在する種別だけ


def world(db, tmp_path):
    w = World()
    w.point(10, 130.39, 33.585, amenity="place_of_worship", name="警固神社")
    w.run(db, tmp_path)


def wiki(pid, title, lat, lon):
    return {"pageid": pid, "title": title, "lat": lat, "lon": lon}


def test_wikipedia_candidates_skip_near_same_name_outside_and_admin(db, tmp_path):
    world(db, tmp_path)
    pts = {1: wiki(1, "新しい図書館", 33.590, 130.400),            # 候補
           2: wiki(2, "警固神社", 33.5851, 130.3901),              # 近くに既存の地点
           3: wiki(3, "別の場所の警固神社", 33.5990, 130.4400),    # 同名に近い名前だが 1km 以上離れている → 候補
           4: wiki(4, "海外の駅", 33.45, 130.25),                  # 7区の外
           5: wiki(5, "博多区", 33.590, 130.410),                  # 行政の項目
           6: wiki(6, "警固神社 (福岡市)", 33.5880, 130.3960)}     # 同名（500m）→ 除く
    st = {}
    got = ac.wikipedia_candidates(db, pts, st)
    names = sorted(r["name"] for r in got)
    assert names == ["別の場所の警固神社", "新しい図書館"]
    lib = [r for r in got if r["name"] == "新しい図書館"][0]
    assert lib["suggest_cat"] == "civic" and lib["area_code"] and lib["source_url"].startswith("https://ja.wikipedia.org/wiki/")
    assert st["skipped_near"] == 1 and st["skipped_same_name"] == 1 and st["skipped_admin"] == 1 and st["skipped_outside"] == 1


def test_commons_candidates_need_enough_photos_and_no_nearby_spot(db, tmp_path):
    world(db, tmp_path)
    def files(n, lat, lon, base):
        return {base + i: {"pageid": base + i, "title": f"File:Photo {base + i}.jpg", "lat": lat + i * 1e-6, "lon": lon} for i in range(n)}
    pts = {**files(6, 33.5900, 130.4000, 100),         # 6枚・近くに地点なし → 候補
           **files(3, 33.5950, 130.4100, 200),         # 3枚 → 足りない
           **files(8, 33.5850, 130.3900, 300)}         # 既存の地点のそば → 除く
    st = {}
    got = ac.commons_candidates(db, pts, st)
    assert len(got) == 1 and got[0]["files"] == 6 and got[0]["name"].startswith("（名称未設定）")
    assert got[0]["source_key"] == "commons:33.590,130.400" and len(got[0]["samples"]) == 5 and st["commons_skipped_near"] == 1


def test_save_is_idempotent_and_never_overwrites_decisions(db, tmp_path):
    world(db, tmp_path)
    rows = ac.wikipedia_candidates(db, {1: wiki(1, "新しい図書館", 33.590, 130.400)}, {})
    assert ac.save(db, rows) == 1
    aid = db.execute("select id from spot_additions").fetchone()["id"]
    assert rpc(db, "addition_decide", p_id=aid, p_decision="reject", p_user="hirata")["ok"]
    assert ac.save(db, rows) == 0                                           # 2回目は入らない
    assert db.execute("select status from spot_additions").fetchone()["status"] == "rejected"   # 決めた状態は残る
    assert rpc(db, "addition_decide", p_id=aid, p_decision="reset", p_user="hirata")["ok"]
    assert db.execute("select status from spot_additions").fetchone()["status"] == "candidate"


def test_adopt_creates_manual_spot_with_source_and_requires_human_input(db, tmp_path):
    world(db, tmp_path)
    ac.save(db, ac.wikipedia_candidates(db, {1: wiki(1, "新しい図書館", 33.590, 130.400)}, {})
            + ac.commons_candidates(db, {i: {"pageid": i, "title": f"File:P{i}.jpg", "lat": 33.5955, "lon": 130.4105}
                                         for i in range(6)}, {}))
    wid = db.execute("select id from spot_additions where source = 'wikipedia'").fetchone()["id"]
    cid = db.execute("select id from spot_additions where source = 'commons'").fetchone()["id"]
    assert not rpc(db, "addition_decide", p_id=wid, p_decision="adopt", p_user="")["ok"]             # 名前のない操作は不可
    assert not rpc(db, "addition_decide", p_id=wid, p_decision="adopt", p_user="hirata")["ok"]        # 種別がない
    assert not rpc(db, "addition_decide", p_id=cid, p_decision="adopt", p_user="hirata", p_category="park")["ok"]   # 名前を付けていない
    r = rpc(db, "addition_decide", p_id=wid, p_decision="adopt", p_user="hirata", p_name="新しい図書館", p_category="civic")
    assert r["ok"] and r["code"].startswith("FUK-")
    s = db.execute("select * from spots where id = %s", (r["spot_id"],)).fetchone()
    assert s["origin"] == "manual" and s["confidence"] == "unverified" and s["category"] == "civic" and s["summary"] is None
    src = db.execute("select url, note from sources where spot_id = %s", (s["id"],)).fetchone()
    assert src["url"].startswith("https://ja.wikipedia.org/") and "Wikipedia" in src["note"]
    a = db.execute("select status, spot_id, decided_by from spot_additions where id = %s", (wid,)).fetchone()
    assert (a["status"], a["spot_id"], a["decided_by"]) == ("adopted", s["id"], "hirata")
    assert not rpc(db, "addition_decide", p_id=wid, p_decision="adopt", p_user="hirata", p_name="x", p_category="civic")["ok"]   # 二重採用不可
    assert not rpc(db, "addition_decide", p_id=wid, p_decision="reset", p_user="hirata")["ok"]       # 採用済みは戻せない
    r2 = rpc(db, "addition_decide", p_id=cid, p_decision="adopt", p_user="hirata", p_name="港の見える坂", p_category="hill")
    assert r2["ok"]
    j = rpc(db, "additions_counts")
    assert j["adopted"] == 2 and j["candidate"] == 0
    assert rpc(db, "additions_list", p_status="adopted")[0]["spot_code"]
