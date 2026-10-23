"""回帰テスト：写真候補（Wikimedia Commons。DB変更200）。外部へは出ない（応答は手で作る）"""
import pytest

from locadb import commons_photos as cp
from osmworld import World, spots


def page(title, lic="CC BY-SA 4.0", author="<a href='x'>山田 太郎</a>", lat=33.5851, lon=130.3901, nonfree=None, thumb=True,
         desc="警固神社の<b>本殿</b>"):
    return {"title": title, "coordinates": [{"lat": lat, "lon": lon}],
            "imageinfo": [{"thumburl": "https://upload.wikimedia.org/x/640px-a.jpg" if thumb else None,
                           "descriptionurl": "https://commons.wikimedia.org/wiki/" + title.replace(" ", "_"),
                           "extmetadata": {"LicenseShortName": {"value": lic}, "Artist": {"value": author},
                                           "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
                                           "ImageDescription": {"value": desc},
                                           "NonFree": {"value": nonfree}}}]}


SPOT = {"id": 1, "name": "警固神社", "lat": 33.585, "lon": 130.39}


def test_license_rules():
    ok = ["CC BY-SA 4.0", "CC BY 3.0", "CC0", "Public domain", "CC BY-SA 2.5 JP"]
    ng = ["CC BY-NC 4.0", "CC BY-ND 2.0", "Fair use", "", "All rights reserved", "CC BY-NC-SA 3.0"]
    assert all(cp.license_ok(x) for x in ok) and not any(cp.license_ok(x) for x in ng)
    assert not cp.license_ok("CC BY 4.0", "true")


def test_candidates_filter_rank_and_clean():
    data = {"query": {"pages": [
        page("File:Kego Shrine 警固神社.jpg", lat=33.5851),                       # 名前一致・近い
        page("File:Street nearby.jpg", lat=33.5853, desc="a street"),                              # 名前なし・少し遠い
        page("File:NC photo.jpg", lic="CC BY-NC 4.0"),                           # NC → 除く
        page("File:No author.jpg", author=""),                                    # 作者なし → 除く
        page("File:No thumb.jpg", thumb=False),                                   # 画像なし → 除く
    ]}}
    got = cp.candidates_of(SPOT, data)
    assert [c["file_title"] for c in got] == ["File:Kego Shrine 警固神社.jpg", "File:Street nearby.jpg"]
    assert got[0]["name_match"] and not got[1]["name_match"]
    assert got[0]["author"] == "山田 太郎" and "<" not in got[0]["description"]       # HTMLは除く
    assert got[0]["dist_m"] < got[1]["dist_m"]


def test_collect_apply_decide_and_resume(db, tmp_path):
    w = World()
    w.point(10, 130.39, 33.585, amenity="place_of_worship", name="警固神社")
    w.point(11, 130.40, 33.590, amenity="place_of_worship", name="寺B")
    w.run(db, tmp_path)
    by = {s["name"]: s for s in spots(db)}

    class Sess:
        def get(self, url, params=None, timeout=None, headers=None):
            class R:
                status_code = 200
                def json(_):
                    lat = float(params["ggscoord"].split("|")[0])
                    return {"query": {"pages": [page("File:Kego Shrine 警固神社.jpg")]}} if abs(lat - 33.585) < 1e-3 else {}
            return R()

    found, st, marks = cp.collect(db, 10, Sess(), sleep=lambda s: None)
    assert st["spots"] == 2 and st["with_candidates"] == 1 and st["candidates"] == 1
    with db.cursor() as cur:
        cur.executemany(cp.INSERT_SQL, found); cur.executemany(cp.MARK_SQL, marks)
    db.commit()
    # 探した地点は次回に含めない（候補なしの地点も）
    assert cp.collect(db, 10, Sess(), sleep=lambda s: None)[1]["spots"] == 0
    # 採否：採用すると出典つきで spot_photos に入る。公開の地点詳細にも出る
    cand = db.execute("select photo_candidates_list() r").fetchone()["r"][0]
    assert cand["name"] == "警固神社" and cand["status"] == "candidate"
    assert db.execute("select photos_counts() r").fetchone()["r"]["candidate"] == 1
    assert not db.execute("select photo_decide(%s,'adopt','') r", (cand["id"],)).fetchone()["r"]["ok"]      # 人の名前が必要
    assert db.execute("select photo_decide(%s,'adopt','hirata') r", (cand["id"],)).fetchone()["r"]["ok"]
    ph = db.execute("select spot_detail(%s) r", (by["警固神社"]["id"],)).fetchone()["r"]["photos"]
    assert len(ph) == 1 and ph[0]["author"] == "山田 太郎" and ph[0]["license"] == "CC BY-SA 4.0" and ph[0]["source_url"].startswith("https://")
    row = db.execute("select search_spots(p_q => '警固神社') r").fetchone()["r"][0]       # 一覧にも出典つきで出る
    assert row["photo"]["author"] == "山田 太郎" and row["photo"]["license"] == "CC BY-SA 4.0"
    # 候補に戻すと写真も外れる／不採用
    db.execute("select photo_decide(%s,'reset','hirata')", (cand["id"],))
    assert db.execute("select spot_detail(%s) r", (by["警固神社"]["id"],)).fetchone()["r"]["photos"] == []
    db.execute("select photo_decide(%s,'reject','hirata')", (cand["id"],))
    assert db.execute("select photos_counts() r").fetchone()["r"] == {"candidate": 0, "adopted": 0, "rejected": 1, "spots": 0}


def test_photo_state_and_filter(db, tmp_path):
    """写真の状態：todo（未探索）→ none（探索済み・候補なし）→ cand（候補あり）→ has（採用）。絞り込みと表示に使う"""
    w = World()
    w.point(10, 130.39, 33.585, amenity="place_of_worship", name="警固神社")
    w.point(11, 130.40, 33.590, amenity="place_of_worship", name="寺B")
    w.run(db, tmp_path)
    by = {s["name"]: s for s in spots(db)}
    a, b = by["警固神社"], by["寺B"]
    state = lambda sp: db.execute("select photo_state(%s,%s,false) r", (sp["id"], sp["category"])).fetchone()["r"]
    assert state(a) == "todo" and state(b) == "todo"
    assert db.execute("select photo_state(%s,'barber',false) r", (a["id"],)).fetchone()["r"] == "na"        # 対象外の種別
    assert db.execute("select photo_state(%s,%s,true) r", (a["id"], a["category"])).fetchone()["r"] == "na"  # 無名は対象外
    db.execute("select 1")
    db.execute("insert into spot_photo_search (spot_id, found) values (%s,0),(%s,1)", (a["id"], b["id"]))
    db.execute("""insert into spot_photo_candidates (spot_id, file_title, page_url, image_url, author, license)
                  values (%s,'File:x.jpg','https://c/x','https://c/x.jpg','作者','CC BY 4.0')""", (b["id"],))
    db.commit()
    assert state(a) == "none" and state(b) == "cand"
    got = lambda st: {r["name"] for r in db.execute("select search_spots(p_photo => %s) r", (st,)).fetchone()["r"]}
    assert got("none") == {"警固神社"} and got("cand") == {"寺B"} and got("todo") == set() and got("has") == set()
    assert {r["name"]: r["photo_state"] for r in db.execute("select search_spots() r").fetchone()["r"]} == {"警固神社": "none", "寺B": "cand"}
    cid = db.execute("select id from spot_photo_candidates where spot_id=%s", (b["id"],)).fetchone()["id"]
    db.execute("select photo_decide(%s,'adopt','hirata')", (cid,))
    assert state(b) == "has" and got("has") == {"寺B"}
    assert db.execute("select spot_detail(%s) r", (b["id"],)).fetchone()["r"]["photo_state"] == "has"


def test_photo_suggest_bulk_and_reject_reason(db, tmp_path):
    """おすすめ度（暫定ルール）・絞り込み・まとめて採用（見込み高だけ）・不採用の理由"""
    sg = lambda *a: db.execute("select photo_suggest(%s,%s,%s,%s) r", a).fetchone()["r"]
    assert sg("commons", True, "境内の風景", "File:a.jpg") == "high"
    assert sg("commons", True, "駅名標の写真", "File:a.jpg") == "low"          # 看板類の語が優先
    assert sg("commons", True, "A sign at the gate", "File:a.jpg") == "low"
    assert sg("commons", False, "境内", "File:a.jpg") == "unknown"
    assert sg("mapillary", False, None, None) == "unknown"
    assert sg("commons", True, "design", "File:a.jpg") == "high"              # 語の途中（design の sign）は一致させない

    w = World()
    w.point(10, 130.39, 33.585, amenity="place_of_worship", name="警固神社")
    w.run(db, tmp_path)
    sid = spots(db)[0]["id"]
    ids = {}
    for key, nm, desc in [("hi", True, "境内"), ("lo", True, "案内看板"), ("un", False, "境内")]:
        ids[key] = db.execute("""insert into spot_photo_candidates (spot_id, file_title, page_url, image_url, author, license, name_match, description, source)
                                 values (%s,%s,%s,%s,'作者','CC BY 4.0',%s,%s,'commons') returning id""",
                              (sid, f"File:{key}.jpg", f"https://c/{key}", f"https://c/{key}.jpg", nm, desc)).fetchone()["id"]
    db.commit()
    lst = db.execute("select photo_candidates_list() r").fetchone()["r"]
    assert [c["suggest"] for c in lst] == ["high", "unknown", "low"]            # 見込み高→判断材料なし→見込み低
    assert [c["id"] for c in db.execute("select photo_candidates_list(p_suggest => 'low') r").fetchone()["r"]] == [ids["lo"]]
    assert db.execute("select photos_suggest_counts() r").fetchone()["r"] == {"high": 1, "unknown": 1, "low": 1}
    # まとめて採用：名前が必要。見込み高だけ通り、ほかは変えない
    assert not db.execute("select photo_decide_bulk(%s,'adopt','') r", ([ids["hi"]],)).fetchone()["r"]["ok"]
    r = db.execute("select photo_decide_bulk(%s,'adopt','hirata') r", ([ids["hi"], ids["lo"], ids["un"]],)).fetchone()["r"]
    assert r == {"ok": True, "decided": 1, "skipped": 2}
    st = {r["id"]: r["status"] for r in db.execute("select id, status from spot_photo_candidates").fetchall()}
    assert st == {ids["hi"]: "adopted", ids["lo"]: "candidate", ids["un"]: "candidate"}
    # 決め済みは対象外（もう一度押しても変わらない）
    assert db.execute("select photo_decide_bulk(%s,'adopt','hirata') r", ([ids["hi"]],)).fetchone()["r"]["decided"] == 0
    # 不採用の理由：まとめて不採用／個別。正しくない理由は断る
    assert not db.execute("select photo_decide(%s,'reject','hirata','bogus') r", (ids["un"],)).fetchone()["r"]["ok"]
    assert db.execute("select photo_decide(%s,'reject','hirata','person') r", (ids["un"],)).fetchone()["r"]["ok"]
    assert db.execute("select photo_decide_bulk(%s,'reject','hirata','sign') r", ([ids["lo"]],)).fetchone()["r"]["decided"] == 1
    rr = {r["id"]: r["reject_reason"] for r in db.execute("select id, reject_reason from spot_photo_candidates").fetchall()}
    assert rr[ids["un"]] == "person" and rr[ids["lo"]] == "sign" and rr[ids["hi"]] is None
    db.execute("select photo_decide(%s,'reset','hirata')", (ids["un"],))
    assert db.execute("select reject_reason from spot_photo_candidates where id=%s", (ids["un"],)).fetchone()["reject_reason"] is None


def test_reject_closed_archives_spot(db, tmp_path):
    """理由「現存しない」で不採用＋確認ずみなら、地点を対象外にし、同じ地点の未決の候補もまとめて不採用にする。戻せる"""
    w = World()
    w.point(10, 130.39, 33.585, amenity="place_of_worship", name="警固神社")
    w.point(11, 130.40, 33.590, amenity="place_of_worship", name="寺B")
    w.run(db, tmp_path)
    by = {s["name"]: s for s in spots(db)}
    ids = []
    for sp, k in [(by["警固神社"], "a"), (by["警固神社"], "b"), (by["寺B"], "c")]:
        ids.append(db.execute("""insert into spot_photo_candidates (spot_id, file_title, page_url, image_url, author, license)
                                 values (%s,%s,%s,%s,'作者','CC BY 4.0') returning id""",
                              (sp["id"], f"File:{k}.jpg", f"https://c/{k}", f"https://c/{k}.jpg")).fetchone()["id"])
    db.commit()
    # 確認なし（p_archive=false）では、理由だけを記録して地点はそのまま
    assert db.execute("select photo_decide(%s,'reject','hirata','closed') r", (ids[0],)).fetchone()["r"]["archived"] is False
    assert db.execute("select data_status from spots where id=%s", (by["警固神社"]["id"],)).fetchone()["data_status"] != "archived"
    r = db.execute("select photo_decide(%s,'reject','hirata','closed',true) r", (ids[1],)).fetchone()["r"]
    assert r["ok"] and r["archived"] is True
    sp = db.execute("select data_status, archived_reason from spots where id=%s", (by["警固神社"]["id"],)).fetchone()
    assert sp["data_status"] == "archived" and "現存しない" in sp["archived_reason"]
    st = {r["id"]: (r["status"], r["reject_reason"]) for r in db.execute("select id, status, reject_reason from spot_photo_candidates").fetchall()}
    assert st[ids[0]] == ("rejected", "closed") and st[ids[1]] == ("rejected", "closed") and st[ids[2]][0] == "candidate"
    # 理由が closed 以外なら p_archive は効かない
    db.execute("select photo_decide(%s,'reject','hirata','person',true)", (ids[2],))
    assert db.execute("select data_status from spots where id=%s", (by["寺B"]["id"],)).fetchone()["data_status"] != "archived"
    # 戻せる
    db.execute("select set_spot_state(%s,'restore','hirata')", (by["警固神社"]["id"],))
    assert db.execute("select data_status from spots where id=%s", (by["警固神社"]["id"],)).fetchone()["data_status"] == "current"


def test_photo_suggest_v2_rules_and_auto_reason(db, tmp_path):
    """改訂したおすすめ度（種別・距離を見る）と、まとめて不採用の自動の理由"""
    sg = lambda *a: db.execute("select photo_suggest(%s,%s,%s,%s,%s,%s) r", a).fetchone()["r"]
    assert sg("commons", True, "境内", "File:a.jpg", "heritage", 20) == "high"            # 名前一致は種別・距離によらず見込み高
    assert sg("commons", False, "境内", "File:a.jpg", "station", 50) == "low"             # 名前不一致で30m以上
    assert sg("commons", False, "境内", "File:a.jpg", "school", 5) == "low"               # 名前不一致の学校
    assert sg("commons", False, "境内", "File:a.jpg", "heritage", 5) == "low"
    assert sg("commons", False, "境内", "File:a.jpg", "station", 5) == "unknown"          # 近くて学校・史跡以外は要確認
    assert sg("mapillary", False, None, None, "heritage", 5) == "low"
    assert sg("mapillary", False, None, None, "commercial_facility", 5) == "unknown"
    assert sg("mapillary", False, None, None) == "unknown"                              # 種別が分からなければ見ない
    rs = lambda *a: db.execute("select photo_suggest_reason(%s,%s,%s) r", a).fetchone()["r"]
    assert rs("commons", "駅名標", "File:a.jpg") == "sign" and rs("mapillary", "x", "File:m.jpg") == "quality"
    assert rs("commons", "境内", "File:a.jpg") == "wrong_place"

    w = World()
    w.point(10, 130.39, 33.585, amenity="school", name="A小学校")
    w.run(db, tmp_path)
    sp = spots(db)[0]
    ids = []
    for k, nm, src in [("a", False, "commons"), ("b", True, "commons")]:
        ids.append(db.execute("""insert into spot_photo_candidates (spot_id, file_title, page_url, image_url, author, license, name_match, source, dist_m)
                                 values (%s,%s,%s,%s,'作者','CC BY 4.0',%s,%s,10) returning id""",
                              (sp["id"], f"File:{k}.jpg", f"https://c/{k}", f"https://c/{k}.jpg", nm, src)).fetchone()["id"])
    db.commit()
    cat = sp["category"]
    got = {c["id"]: c["suggest"] for c in db.execute("select photo_candidates_list() r").fetchone()["r"]}
    assert got[ids[1]] == "high" and (got[ids[0]] == "low" if cat in ("school", "civic", "heritage") else True)
    if cat in ("school", "civic", "heritage"):
        # まとめて不採用は「見込み低」だけ。理由は自動（別の場所）。見込み高は不採用にできない
        r = db.execute("select photo_decide_bulk(%s,'reject','hirata') r", ([ids[0], ids[1]],)).fetchone()["r"]
        assert r == {"ok": True, "decided": 1, "skipped": 1}
        row = db.execute("select status, reject_reason from spot_photo_candidates where id=%s", (ids[0],)).fetchone()
        assert (row["status"], row["reject_reason"]) == ("rejected", "wrong_place")
