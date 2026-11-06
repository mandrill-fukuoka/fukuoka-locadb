"""回帰テスト：BODIK の情報を既存の地点の事実カードとして足す（locadb/bodik_facts.py）。外部へは出ない（取得は偽のセッションで差し替える）"""
import json
from datetime import date

from locadb import bodik_facts as bf
from locadb import bodik_import as bi
from osmworld import World, spot

TODAY = date(2026, 11, 6)


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


def sp(i, name, lat, lon, alt=None):
    return {"id": i, "code": f"FUK-{i}", "name": name, "alt_names": alt or [], "lat": lat, "lon": lon}


def gsi(title, lat, lon):
    return [{"geometry": {"coordinates": [lon, lat]}, "properties": {"title": title}}]


# ------------------------------------------------------------------ 決まり（DBなし）
def test_norm_ignores_width_spaces_prefix_company_and_parentheses():
    assert bf.norm("福岡市立 和白幼稚園") == bf.norm("和白幼稚園") == "和白幼稚園"
    assert bf.norm("ＨＯＴＥＬ　ニュー・オータニ（博多）") == bf.norm("hotel ニューオータニ")
    assert bf.norm("株式会社テスト食堂") == "テスト食堂"
    assert bf.norm("") == ""


def test_scale_text_matches_existing_fact_style():
    assert bf.scale_text(533) == "533m2" and bf.scale_text(9999.4) == "9,999m2"
    assert bf.scale_text(43600) == "4.36ヘクタール" and bf.scale_text(400000) == "40ヘクタール" and bf.scale_text(120400) == "12.04ヘクタール"


def test_source_names_dataset_page_title_and_license():
    s = bf.source_of("inn")
    assert s == {"source_kind": "web", "source_url": "https://data.bodik.jp/dataset/401307_ryokangyou_eigyoukyoka",
                 "source_title": "BODIK ODCS 福岡市「旅館業営業許可施設一覧」", "license": "CC BY 4.0"}


def test_inn_claims_use_rooms_and_capacity_and_never_read_names():
    rec = [{"旅館業の種別": "ホテル", "施設名称": "ホテルＡ", "施設所在地": "福岡市博多区住吉二丁目１４番１号", "営業者氏名": "山田太郎",
            "客室数": "120", "定員数": "300", "許可年月日": "R03.04.01"},
           {"旅館業の種別": "簡易宿所", "施設名称": "宿B", "施設所在地": "福岡市中央区天神一丁目１番", "客室数": "0", "定員数": "6"},
           {"旅館業の種別": "旅館", "施設名称": "宿C", "施設所在地": "福岡市中央区天神一丁目２番", "客室数": "", "定員数": ""}]
    got = bf.inn_claims(rec, TODAY)
    assert [(c["name"], c["value"], c["item"]) for c in got] == [("ホテルA", "客室120室・定員300人", "規模"), ("宿B", "定員6人", "規模")]
    assert got[0]["note"] == "旅館業営業許可の届出内容（ホテル）" and got[0]["as_of"] == "2026年11月取得（福岡市オープンデータ）"
    assert "山田" not in json.dumps(got, ensure_ascii=False) and "R03" not in json.dumps(got, ensure_ascii=False)   # 許可年月日は使わない


def test_eatery_claims_use_first_permit_year_and_skip_expired():
    rec = [{"屋号": "笑伝", "営業所所在地": "福岡市博多区店屋町２番２０号", "許可終了日": "2026/9/30", "初年度許可・交付日": "2020/7/2"},
           {"屋号": "新しい店", "営業所所在地": "福岡市博多区店屋町３番１号", "許可終了日": "2031/3/31", "初年度許可・交付日": "2019/4/1"},
           {"屋号": "年なし", "営業所所在地": "福岡市博多区店屋町４番１号", "許可終了日": "2031/3/31", "初年度許可・交付日": ""}]
    got = bf.eatery_claims(rec, TODAY)
    assert [(c["name"], c["item"], c["value"]) for c in got] == [("新しい店", "創設・開設年", "2019年（営業許可の初年度）")]
    assert "開業年とは限らない" in got[0]["note"]


def test_waterspot_and_park_claims():
    rec = [{"名称": "東区役所", "緯度": "33.6177", "経度": "130.4173", "団体名": "福岡市"},
           {"名称": "団体名なし", "緯度": "33.6", "経度": "130.4", "団体名": ""},
           {"名称": "圏外", "緯度": "35.0", "経度": "139.0", "団体名": "福岡市"}]
    w = bf.waterspot_claims(rec)
    assert [(c["name"], c["item"], c["value"], c["note"]) for c in w] == [("東区役所", "管理者・所有者", "福岡市", "給水スポットの設置団体")]
    p = bf.park_claims([{"name": "大きな公園", "lat": 33.59, "lon": 130.40, "rank": 40000.0, "detail": {"公園種別": "近隣公園", "区名": "中央区"}},
                        {"name": "面積なし", "lat": 33.59, "lon": 130.40, "rank": 0.0, "detail": {}}])
    assert len(p) == 1 and p[0]["value"] == "4ヘクタール" and p[0]["note"] == "供用面積（近隣公園）"
    assert p[0]["radius"] == 150 + 200                                                    # 広い公園は探す範囲を広げる


# ------------------------------------------------------------------ 結びつけ（DBなし）
def claim(name, lat=None, lon=None, addr=None, value="客室10室", item="規模", ds="inn", radius=150):
    return {"dataset": ds, "name": name, "addr": addr, "lat": lat, "lon": lon, "radius": radius, "item": item, "value": value,
            "note": "n", "as_of": None, "quote": "q"}


def test_match_requires_same_name_and_nearby_and_skips_ambiguous_conflicting_and_existing():
    idx = bf.SpotIndex([sp(1, "ホテルA", 33.5900, 130.4000), sp(2, "ホテルB", 33.5950, 130.4100),
                        sp(3, "ホテルC", 33.6000, 130.4200), sp(4, "ホテルC", 33.6001, 130.4201),      # 同名が近くに2つ
                        sp(5, "ホテルD", 33.6100, 130.4300), sp(6, "別名の宿", 33.6200, 130.4400, ["宿E"])])
    claims = [claim("ホテルA", 33.5901, 130.4001),                         # 一致
              claim("ホテルＢ", 33.6500, 130.5000),                         # 名前は一致だが遠い
              claim("ホテルC", 33.6000, 130.4200),                          # 同名が複数 → 足さない
              claim("ホテルD", 33.6100, 130.4300), claim("ホテルD", 33.6100, 130.4300, value="客室99室"),   # 別の値が2つ
              claim("宿E", 33.6200, 130.4401),                              # alt_names で一致
              claim("存在しない宿", 33.6, 130.4),
              claim("ホテルA", 33.5901, 130.4001, item="創設・開設年", value="2000年")]     # すでに承認済みの項目
    st = {}
    facts, left = bf.match_claims(claims, idx, {(1, "創設・開設年")}, st)
    assert sorted((f["spot_id"], f["item"], f["value"]) for f in facts) == [(1, "規模", "客室10室"), (6, "規模", "客室10室")]
    assert {(c["name"], c["why"]) for c in left} == {("ホテルC", "同じ名前の地点が近くに複数ある"), ("ホテルD", "同じ地点に別の値がある")}
    s = st["inn"]
    assert (s["claims"], s["facts"], s["name_but_far"], s["no_spot_name"], s["ambiguous_spots"], s["conflicting_values"], s["spot_has_fact"]) == (8, 2, 1, 1, 1, 1, 1)
    f = [f for f in facts if f["spot_id"] == 1][0]
    assert f["source_kind"] == "web" and f["license"] == "CC BY 4.0" and f["_csv"][2:4] == ["FUK-1", "ホテルA"] and f["_csv"][4] <= 20


def test_address_only_items_are_geocoded_only_when_the_name_is_known():
    idx = bf.SpotIndex([sp(1, "ホテルA", 33.5906, 130.4141)])
    sess = FakeSession({"AddressSearch": gsi("福岡県福岡市博多区博多駅前2丁目8-1", 33.5906, 130.4141)})
    claims = [claim("ホテルA", addr="福岡市博多区博多駅前二丁目８番１号"), claim("知らない宿", addr="福岡市博多区博多駅前二丁目９番１号")]
    st = {}
    facts, _ = bf.match_claims(claims, idx, set(), st, sess, no_sleep)
    assert [f["spot_id"] for f in facts] == [1] and len(sess.calls) == 1               # 名前が地点にあるものだけ住所検索
    bad = FakeSession({"AddressSearch": gsi("福岡県福岡市博多区博多駅前", 33.59, 130.41)})      # 番地まで引けない
    st2 = {}
    facts2, _ = bf.match_claims(claims[:1], idx, set(), st2, bad, no_sleep)
    assert facts2 == [] and st2["inn"]["geocode_failed"] == 1


def test_geocode_limit_counts_per_dataset():
    idx = bf.SpotIndex([sp(1, "A", 33.5906, 130.4141), sp(2, "B", 33.5906, 130.4141)])
    sess = FakeSession({"AddressSearch": gsi("福岡県福岡市博多区博多駅前2丁目8-1", 33.5906, 130.4141)})
    st = {}
    facts, _ = bf.match_claims([claim("A", addr="福岡市博多区博多駅前二丁目８番１号"), claim("B", addr="福岡市博多区博多駅前二丁目８番１号")], idx, set(), st, sess, no_sleep, limit=1)
    assert len(facts) == 1 and st["inn"]["geocode_skipped_limit"] == 1


def test_targets_are_valid_and_not_the_ones_we_decided_against():
    assert set(bf.TARGETS) <= set(bi.SPEC_BY_KEY) and "bath" not in bf.TARGETS and "wifi" not in bf.TARGETS and "public_facility" not in bf.TARGETS


# ------------------------------------------------------------------ DB（ci で動く）
def test_spots_sql_import_dry_run_rolls_back_and_apply_commits(db, tmp_path):
    w = World()
    w.point(10, 130.40, 33.59, amenity="place_of_worship", name="寺A")
    w.point(11, 130.41, 33.60, amenity="place_of_worship", name="寺B")
    w.run(db, tmp_path)
    idx = bf.SpotIndex(db.execute(bf.SPOTS_SQL).fetchall())
    a = spot(db, name="寺A")
    facts, left = bf.match_claims([claim("寺Ａ", a["lat"], a["lon"])], idx, set(), {})
    assert len(facts) == 1 and facts[0]["spot_id"] == a["id"] and left == []
    n = lambda: db.execute("select count(*) n from spot_facts").fetchone()["n"]
    r = bf.run_import(db, facts, "hirata", apply=False)
    assert (r["added"], r["committed"], n()) == (1, False, 0)                          # 試しは取り消す
    r = bf.run_import(db, facts, "hirata", apply=True)
    db.commit()
    assert (r["added"], r["committed"], n()) == (1, True, 1)
    row = db.execute("select item, value, source_kind, source_url, license, approved_by, status from spot_facts").fetchone()
    assert (row["item"], row["value"], row["source_kind"], row["license"], row["approved_by"], row["status"]) == ("規模", "客室10室", "web", "CC BY 4.0", "hirata", "approved")
    assert row["source_url"].startswith("https://data.bodik.jp/dataset/")
    again = bf.run_import(db, facts, "hirata", apply=True)
    assert (again["added"], again["skipped"]) == (0, 1)                                 # 何度実行しても同じ
    bad = [dict(facts[0], source_url=None)]
    r = bf.run_import(db, bad, "hirata", apply=True)
    assert r["committed"] is False and len(r["rejected"]) == 1                          # 弾かれたら取り込まない
