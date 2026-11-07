"""回帰テスト：福岡市のAED設置場所を取り込み、地点ページの「近くのAED」に出す（locadb/bodik_aed.py・DB変更230）。外部へは出ない"""
import json

import pytest

from locadb import bodik_aed as ba
from osmworld import World, spots


def no_sleep(_):
    return None


class Resp:
    def __init__(self, body=b"", status=200):
        self.status_code, self.content = status, body

    def json(self):
        return json.loads(self.content.decode("utf-8"))


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url, params))
        for key, body in self.routes.items():
            if key in url:
                return Resp(body if isinstance(body, bytes) else json.dumps(body).encode("utf-8"))
        return Resp(b"", 404)


def gsi(title, lat, lon):
    return [{"geometry": {"coordinates": [lon, lat]}, "properties": {"title": title}}]


# ------------------------------------------------------------------ 決まり（DBなし）
def test_find_package_prefers_the_fukuoka_aed_dataset():
    sess = FakeSession({"package_search": {"result": {"results": [
        {"name": "x1", "title": "福岡市 避難所"}, {"name": "401307_aed_other", "title": "福岡市 ＡＥＤ設置施設（旧）"},
        {"name": "401307_aed", "title": "福岡市 AED"}]}}})
    assert ba.find_package(sess, no_sleep)["name"] == "401307_aed"
    assert sess.calls[0][1]["fq"] == "organization:401307"
    assert ba.find_package(FakeSession({"package_search": {"result": {"results": [{"name": "a", "title": "福岡市 公園"}]}}}), no_sleep) is None


def test_license_label():
    assert ba.license_label({"license_id": "cc-by"}) == "CC BY 4.0" and ba.license_label({}) == "CC BY 4.0"
    assert ba.license_label({"license_id": "cc-by-sa", "license_title": "表示-継承"}) == "表示-継承"


def test_split_place_separates_hours_when_written_together():
    assert ba.split_place("設置場所　：　福岡市医師会館8階　講堂前, 使用時間　：　平日9時～17時、土曜日9時～13まで使用可能") == \
        ("福岡市医師会館8階　講堂前", "平日9時～17時、土曜日9時～13まで使用可能")
    assert ba.split_place("1階事務室") == ("1階事務室", "")
    assert ba.split_place("AED設置場所:1階玄関付近 使用時間:24時間使用可能") == ("1階玄関付近", "24時間使用可能")
    assert ba.split_place("設置場所:博多区役所1階守衛室、5階健康課 使用時間:常時使用可能") == ("博多区役所1階守衛室、5階健康課", "常時使用可能")
    assert ba.split_place("設置場所:管理員室") == ("管理員室", "")


def test_points_use_coordinates_then_address_and_skip_the_unusable():
    rec = [{"名称": "福岡市医師会館", "住所": "福岡市早良区百道浜1-6-9", "緯度": "33.592182", "経度": "130.356433",
            "設置位置": "設置場所：8階 講堂前, 使用時間：平日9時～17時", "電話番号": "092-852-1000"},
           {"名称": "座標なしの店", "住所": "福岡市博多区博多駅前2-8-1", "緯度": "", "経度": "", "設置位置": "1階", "利用可能曜日": "毎日",
            "利用可能開始時間": "9:00", "利用可能終了時間": "21:00"},
           {"名称": "自由記述の店", "住所": "福岡市中央区天神1-1-1", "緯度": "33.59", "経度": "130.40", "設置場所": "8592",
            "フリースペース": "AED設置場所:1階玄関付近 使用時間:24時間使用可能"},
           {"名称": "引けない店", "住所": "福岡市博多区どこか", "緯度": "", "経度": ""},
           {"名称": "福岡市医師会館", "住所": "福岡市早良区百道浜1-6-9", "緯度": "33.592182", "経度": "130.356433", "設置位置": "設置場所：8階 講堂前, 使用時間：平日9時～17時"},
           {"名称": "", "住所": "x", "緯度": "33.59", "経度": "130.40"},
           {"名称": "圏外", "住所": "", "緯度": "35.0", "経度": "139.0"}]
    sess = FakeSession({"AddressSearch": gsi("福岡県福岡市博多区博多駅前2丁目8-1", 33.5906, 130.4141)})
    st = {}
    got = ba.points_from_records(rec[:3] + rec[4:], st, sess, no_sleep)
    assert [p["name"] for p in got] == ["福岡市医師会館", "座標なしの店", "自由記述の店"]
    a, b, c = got
    assert (c["place_note"], c["hours_note"]) == ("1階玄関付近", "24時間使用可能")      # 「設置場所」の列（別の値）は使わない
    assert (a["place_note"], a["hours_note"], a["geocoded"]) == ("8階 講堂前", "平日9時~17時", False)
    assert b["geocoded"] is True and b["hours_note"] == "曜日：毎日 開始時間：9:00 終了時間：21:00"
    assert "092" not in json.dumps(got, ensure_ascii=False)                     # 電話番号は読まない
    assert (st["duplicates"], st["skipped_no_name"], st["skipped_no_point"], st["geocoded"]) == (1, 1, 1, 1)
    st2 = {}
    assert ba.points_from_records([rec[3]], st2, FakeSession({"AddressSearch": gsi("福岡県福岡市博多区博多駅前", 33.59, 130.41)}), no_sleep) == [] \
        and st2["geocode_failed"] == 1


# ------------------------------------------------------------------ DB（ciだけで動く）
SRC = {"source_url": "https://data.bodik.jp/dataset/401307_aed", "source_title": "BODIK ODCS 福岡市「AED」", "license": "CC BY 4.0"}


def pt(name, lat, lon, **kw):
    return {"source_key": f"bodik:aed:{name}", "name": name, "address": "住所", "place_note": kw.get("place", ""), "hours_note": kw.get("hours", ""),
            "lat": lat, "lon": lon, "geocoded": kw.get("approx", False)}


def view(db, spot_id):
    row = db.execute("select aed_view(%s) r", (spot_id,)).fetchone()
    db.commit()
    return row["r"]


def test_aed_view_and_import_rules(db, tmp_path):
    w = World()
    w.point(10, 130.39, 33.585, amenity="place_of_worship", name="警固神社")
    w.run(db, tmp_path)
    spot_id = {s["name"]: s["id"] for s in spots(db)}["警固神社"]
    assert view(db, spot_id) is None                                            # 取り込み前は何も出さない
    # 地点（130.39, 33.585）から：約30m・約100m・約200m・約290m・約1km
    pts = [pt("近いAED", 33.585, 130.39033, place="1階ロビー", hours="平日9時～17時"), pt("100m", 33.585, 130.39108, approx=True),
           pt("200m", 33.585, 130.39216), pt("290m", 33.585, 130.39313), pt("遠い", 33.585, 130.40)]
    r = ba.run_import(db, pts, SRC, apply=False)
    assert r["committed"] is False and r["upserted"] == 5
    assert db.execute("select count(*) n from aed_points").fetchone()["n"] == 0     # dry-run は取り消す
    r = ba.run_import(db, pts, SRC, apply=True)
    assert r["committed"] is True and r["active_after"] == 5
    v = view(db, spot_id)
    assert v["radius_m"] == 300 and v["total"] == 4                             # 300m以内は4件、表示は近い順に3件まで
    assert [i["name"] for i in v["items"]] == ["近いAED", "100m", "200m"]
    assert v["items"][0]["meters"] == 30 and v["items"][0]["place"] == "1階ロビー" and v["items"][0]["hours"] == "平日9時～17時"
    assert v["items"][1]["approx"] is True and v["source"]["license"] == "CC BY 4.0" and v["source"]["url"].endswith("401307_aed")
    assert db.execute("select spot_detail(%s) d", (spot_id,)).fetchone()["d"]["aed"]["total"] == 4
    # 取り込み直し：同じ内容は重複しない。一覧から消えたものは active=false で画面から消える
    r = ba.run_import(db, pts[:4], SRC, apply=True)
    assert r["deactivated"] == 1 and db.execute("select count(*) n from aed_points").fetchone()["n"] == 5
    assert view(db, spot_id)["total"] == 4
    with pytest.raises(SystemExit):                                              # 件数が半分未満に減るときは止める（取得の失敗）
        ba.run_import(db, pts[:1], SRC, apply=True)
    r = ba.run_import(db, pts[:1], SRC, apply=False)                             # dry-run は止めずに数を見せる
    assert r["deactivated"] == 3 and r["committed"] is False


def test_nothing_near_gives_empty_list_not_null(db, tmp_path):
    w = World()
    w.point(10, 130.39, 33.585, amenity="place_of_worship", name="警固神社")
    w.run(db, tmp_path)
    spot_id = {s["name"]: s["id"] for s in spots(db)}["警固神社"]
    ba.run_import(db, [pt("遠い", 33.59, 130.41)], SRC, apply=True)
    v = view(db, spot_id)
    assert v["items"] == [] and v["total"] == 0                                 # 登録はあるが近くにない
