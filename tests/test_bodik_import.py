"""回帰テスト：追加候補にBODIK（福岡市オープンデータ）を加える（DB変更220）。外部へは出ない（取得は偽のセッションで差し替える）"""
import json
import re
from datetime import date

import pytest

from locadb import bodik_import as bi
from locadb.categories import CATEGORIES
from osmworld import World


def rpc(db, fn, **kw):
    args = ", ".join(f"{k} => %({k})s" for k in kw)
    row = db.execute(f"select {fn}({args}) r", kw).fetchone()
    db.commit()
    return row["r"]


def no_sleep(_):
    return None


class Resp:
    def __init__(self, body=b"", status=200):
        self.status_code, self.content = status, body

    def json(self):
        return json.loads(self.content.decode("utf-8"))


class FakeSession:
    """url → 返す中身。取得した url と params を覚える"""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url, params))
        for key, body in self.routes.items():
            if key in url:
                return Resp(body if isinstance(body, bytes) else json.dumps(body).encode("utf-8"))
        return Resp(b"", 404)


# ------------------------------------------------------------------ 決まり（DBなし）
def test_specs_use_valid_categories_and_never_read_personal_name_columns():
    assert {s["key"] for s in bi.SPECS} == set(bi.SPEC_BY_KEY) and len(bi.SPECS) == 17
    assert {s["cat"] for s in bi.SPECS if s.get("cat")} <= set(CATEGORIES)
    cols = [c for s in bi.SPECS for k in ("name", "addr", "building", "detail", "id_cols") for c in s.get(k, [])]
    assert not [c for c in cols if re.search(r"氏名|届出者|代表者|営業者", c)]        # 氏名・届出者の列は読まない
    assert "避難" not in " ".join(s["pkg"] + s["label"] for s in bi.SPECS)         # 避難所は別扱い（この取り込みに入れない）


def test_normalize_address_and_precision():
    assert bi.normalize_address("福岡市博多区住吉二丁目１４番１号　３Ｆ　３０１号") == "福岡県福岡市博多区住吉2丁目14-1"
    assert bi.normalize_address("福岡県福岡市中央区長浜二丁目５番") == "福岡県福岡市中央区長浜2丁目5"
    assert bi.normalize_address("福岡市南区井尻一丁目２９番１６－２０２号") == "福岡県福岡市南区井尻1丁目29-16-202"
    assert bi.precise_title("福岡県福岡市中央区天神三丁目８番１号") and bi.precise_title("福岡県福岡市東区千早3丁目6-37")
    assert not bi.precise_title("福岡県福岡市東区西戸崎") and not bi.precise_title("福岡県福岡市中央区天神３丁目")   # 町名・丁目までは使わない


def test_names_and_categories():
    assert bi.display_name("福岡市地下鉄　姪浜駅（ＪＲ姪浜駅）", "subway") == "姪浜駅"
    assert bi.clean("ＢＡＲ　希") == "BAR 希"        # 全角英数・空白を整える（一覧の列を読むときに通る）
    assert bi.sized_name("青葉7号", "") == "青葉7号公園" and bi.sized_name("大濠公園", "") == "大濠公園"
    sp = bi.SPEC_BY_KEY
    assert bi.spec_category(sp["facility"], "福岡市美術館") == "civic" and bi.spec_category(sp["facility"], "福岡市博物館") == "civic"
    assert bi.spec_category(sp["wifi"], "アミカス") is None and bi.spec_category(sp["hoiku"], "舞鶴小放課後児童クラブ") is None
    assert bi.spec_category(sp["building"], "九州大学箱崎キャンパス") is None
    assert bi.spec_category(sp["eatery"], "金山") == "commercial_facility"      # 名前の語（山→hill）に引きずられない
    assert bi.spec_category(sp["school"], "和白幼稚園") == "school" and bi.spec_category(sp["park"], "x") == "park"


def test_items_from_table_skips_closed_stalls_outside_points_and_duplicates():
    rows = [{"名称": "寅政", "緯度": "33.65556", "経度": "130.440253", "区名": "東区", "エリア": "その他", "カテゴリー": "ラーメン",
             "所在地_連結表記": "福岡県福岡市東区千早5-9", "開業／廃業フラグ": "0", "よかなびURL": "https://yokanavi.com/yatai/122472/",
             "画像１": "https://yokanavi.com/x.jpg"},
            {"名称": "さよ子", "緯度": "33.59", "経度": "130.40", "開業／廃業フラグ": "1"},          # 廃業
            {"名称": "海外", "緯度": "35.0", "経度": "135.0", "開業／廃業フラグ": "0"},             # 福岡市の外
            {"名称": "座標なし", "緯度": "", "経度": "", "開業／廃業フラグ": "0"},
            {"名称": "寅政", "緯度": "33.65556", "経度": "130.440253", "開業／廃業フラグ": "0"}]    # 重複
    st = {}
    got = bi.items_from_table(bi.SPEC_BY_KEY["yatai"], rows, "屋台基本情報", st)
    assert [g["name"] for g in got] == ["寅政"] and st == {"skipped_closed": 1, "skipped_no_point": 2}
    d = got[0]["detail"]
    assert d["区名"] == "東区" and d["url"].startswith("https://yokanavi.com/yatai/") and d["resource"] == "屋台基本情報"
    assert "画像" not in json.dumps(d, ensure_ascii=False) and "jpg" not in json.dumps(d)          # 写真は取り込まない
    assert got[0]["key"].startswith("bodik:yatai:")


def test_items_from_address_excludes_adult_baths_expired_permits_and_never_keeps_names():
    spec = bi.SPEC_BY_KEY["bath"]
    st = {}
    got = bi.items_from_address(spec, [
        {"営業の種類": "普通", "施設名称": "阿蘇の湯", "施設所在地": "福岡市東区原田二丁目１６番３６号", "営業者氏名": "倉岡　浩臣"},
        {"営業の種類": "個室付（風営法）", "施設名称": "除く１", "施設所在地": "福岡市中央区中洲一丁目１番１号"},
        {"営業の種類": "個室付（エステ）", "施設名称": "除く２", "施設所在地": "福岡市中央区中洲一丁目１番２号"}], st)
    assert [g["name"] for g in got] == ["阿蘇の湯"] and st == {"skipped_excluded_type": 2}
    assert "倉岡" not in json.dumps(got, ensure_ascii=False)                                   # 氏名はどこにも残らない
    assert "原田" in got[0]["detail"]["address"]

    spec = bi.SPEC_BY_KEY["eatery"]
    base = {"営業所所在地": "福岡市東区舞松原二丁目１１番２４号", "営業所所在地ビル名": "３Ｆ", "業種": "飲食店営業", "業態": "一般食堂",
            "指令番号県記号": "福", "指令番号発令記号": "東保衛"}
    st = {}
    got = bi.items_from_address(spec, [
        {**base, "屋号": "ＢＡＲ　希", "指令番号（許可番号）": "32010054", "許可終了日": "2026/9/30", "営業者氏名": "山田太郎"},     # 期限切れ（今日＝2026-11-05）
        {**base, "屋号": "いまの店", "指令番号（許可番号）": "32010055", "許可終了日": "2027/9/30", "営業者氏名": "山田太郎"},
        {**base, "屋号": "いまの店", "指令番号（許可番号）": "32010055", "許可終了日": "2027/9/30"},                              # 同じ許可番号は1件
        {**base, "屋号": "", "指令番号（許可番号）": "32010056", "許可終了日": "2027/9/30"}], st, today=date(2026, 11, 5))     # 屋号なし
    assert [g["name"] for g in got] == ["いまの店"] and got[0]["key"] == "bodik:eatery:福-東保衛-32010055"
    assert st == {"skipped_expired": 1, "skipped_no_name": 1}
    assert "山田" not in json.dumps(got, ensure_ascii=False) and got[0]["detail"]["業態"] == "一般食堂"


def test_geocode_uses_only_exact_hits_inside_fukuoka():
    def gsi(title, lon, lat):
        return FakeSession({"AddressSearch": [{"geometry": {"coordinates": [lon, lat]}, "properties": {"title": title}}]})
    assert bi.geocode("福岡市中央区天神三丁目８番１号", gsi("福岡県福岡市中央区天神三丁目８番１号", 130.3958, 33.5944), no_sleep) == (33.5944, 130.3958)
    assert bi.geocode("福岡市東区大字西戸崎海の中道海浜公園内", gsi("福岡県福岡市東区西戸崎", 130.3585, 33.6512), no_sleep) is None    # 町名どまり
    assert bi.geocode("福岡市中央区天神三丁目８番１号", gsi("福岡県福岡市中央区天神三丁目８番１号", 135.0, 35.0), no_sleep) is None     # 範囲の外
    s = gsi("福岡県福岡市中央区天神三丁目８番１号", 130.3958, 33.5944)
    bi.geocode("福岡市中央区天神三丁目８番１号", s, no_sleep)
    assert s.calls[0][1] == {"q": "福岡県福岡市中央区天神3丁目8-1"}
    assert bi.geocode("福岡市中央区天神三丁目８番１号", FakeSession({}), no_sleep) is None              # 取れない（404）


def test_collect_reads_csv_in_either_encoding_and_only_csv_resources(tmp_path):
    csv_cp932 = "名称,緯度,経度,住所,分類\n奈多公民館,33.6887,130.4117,福岡市東区奈多2-14-2,公民館\n".encode("cp932")
    pkg = {"result": {"resources": [{"format": "PDF", "url": "https://x/data.pdf", "name": "定義書"},
                                     {"format": "CSV", "url": "https://x/kouminkan.csv", "name": "公民館"}]}}
    s = FakeSession({"package_show": pkg, "kouminkan.csv": csv_cp932})
    st = {}
    items = bi.collect(None, bi.SPEC_BY_KEY["kouminkan"], st, tmp_path, s, no_sleep)
    assert [i["name"] for i in items] == ["奈多公民館"] and all("data.pdf" not in u for u, _ in s.calls)
    assert items[0]["detail"]["分類"] == "公民館" and st["kouminkan_items"] == 1
    assert any(u == bi.CKAN and p == {"id": "401307_kouminkan"} for u, p in s.calls)


# ------------------------------------------------------------------ DB（PostGIS）
def world(db, tmp_path):
    w = World()
    w.point(10, 130.39, 33.585, amenity="place_of_worship", name="警固神社")
    w.run(db, tmp_path)


def it(key, name, lat, lon, **kw):
    return {"key": key, "name": name, "lat": lat, "lon": lon, "detail": kw.pop("detail", {}), "rank": kw.pop("rank", 0.0), **kw}


def test_candidates_skip_near_same_name_outside_and_duplicates(db, tmp_path):
    world(db, tmp_path)
    spec = bi.SPEC_BY_KEY["toshokan"]
    items = [it("bodik:toshokan:1", "新しい図書館", 33.590, 130.400),              # 候補
             it("bodik:toshokan:2", "近くの建物", 33.5851, 130.3901),             # 既存の地点の150m以内
             it("bodik:toshokan:3", "警固神社", 33.5880, 130.3960),              # 同じ名前が500m
             it("bodik:toshokan:4", "別の場所の警固神社", 33.5990, 130.4400),     # 似た名前でも1km以上離れている → 候補
             it("bodik:toshokan:5", "海外の図書館", 33.45, 130.25),               # 7区の外
             it("bodik:toshokan:6", "新しい図書館", 33.590, 130.400)]             # 同じ実行の中の重複
    st = {}
    seen = set()
    got = bi.candidates(db, spec, items, st, set(), seen)
    assert sorted(r["name"] for r in got) == ["別の場所の警固神社", "新しい図書館"]
    lib = [r for r in got if r["name"] == "新しい図書館"][0]
    assert (lib["source"], lib["dataset"], lib["suggest_cat"]) == ("bodik", "toshokan", "civic")
    assert lib["source_url"] == "https://data.bodik.jp/dataset/401307_toshokan" and lib["detail"]["dataset_label"] == "図書館"
    assert (st["skipped_near"], st["skipped_same_name"], st["skipped_outside"], st["skipped_dup_candidate"]) == (1, 1, 1, 1)
    # 保存済みの候補は、再び判定しない・別の一覧の同じ施設は出さない
    assert bi.save(db, got) == 2
    st2 = {}
    assert bi.candidates(db, spec, items[:1], st2, {"bodik:toshokan:1"}) == [] and st2 == {"already_saved": 1}
    st3 = {}
    other = bi.SPEC_BY_KEY["shikuyakusho"]
    assert bi.candidates(db, other, [it("bodik:shikuyakusho:9", "新しい図書館", 33.5901, 130.4001)], st3) == [] and st3["skipped_dup_candidate"] == 1


def test_address_items_get_a_rough_position_or_are_dropped(db, tmp_path):
    world(db, tmp_path)
    spec = bi.SPEC_BY_KEY["inn"]
    items = [{"key": "bodik:inn:a", "name": "新しい旅館", "addr": "福岡市中央区天神三丁目８番１号", "lat": None, "lon": None,
              "detail": {"旅館業の種別": "旅館", "address": "福岡市中央区天神三丁目８番１号"}, "rank": 0.0},
             {"key": "bodik:inn:b", "name": "島の宿", "addr": "福岡市東区大字西戸崎海の中道海浜公園内", "lat": None, "lon": None, "detail": {}, "rank": 0.0}]
    s = FakeSession({"AddressSearch": [{"geometry": {"coordinates": [130.395, 33.5905]}, "properties": {"title": "福岡県福岡市中央区天神三丁目８番１号"}}]})
    st = {}
    got = bi.candidates(db, spec, items[:1], st, set(), set(), s, no_sleep)
    assert len(got) == 1 and got[0]["detail"]["geocode"].startswith("国土地理院") and got[0]["suggest_cat"] == "commercial_facility"
    rough = FakeSession({"AddressSearch": [{"geometry": {"coordinates": [130.3585, 33.6512]}, "properties": {"title": "福岡県福岡市東区西戸崎"}}]})
    st = {}
    assert bi.candidates(db, spec, items[1:], st, set(), set(), rough, no_sleep) == [] and st == {"geocode_failed": 1}


def test_park_shape_uses_largest_polygon_point_and_sorts_by_area(db, tmp_path):
    import shapefile
    world(db, tmp_path)
    x, y = db.execute("select st_x(p) x, st_y(p) y from (select ST_Transform(ST_SetSRID(ST_MakePoint(130.40, 33.59), 4326), 2444) p) t").fetchone().values()
    w = shapefile.Writer(str(tmp_path / "park"), shapeType=shapefile.POLYGON, encoding="cp932")
    for f in ("公園種別名", "公園名称", "区名"):
        w.field(f, "C", 40)
    w.field("供用面積", "N", 12, 0)

    def square(cx, cy, half):
        return [[(cx - half, cy - half), (cx - half, cy + half), (cx + half, cy + half), (cx + half, cy - half), (cx - half, cy - half)]]
    w.poly(square(x, y, 100)); w.record("近隣公園", "大きな公園", "中央区", 40000)
    w.poly(square(x + 5000, y, 10)); w.record("幼児公園", "青葉７号", "東区", 400)
    w.poly(square(x, y + 3000, 30)); w.record("近隣公園", "大きな公園", "中央区", 3600)        # 同じ公園の別の区画
    w.close()
    st = {}
    got = sorted(bi.items_from_shape(db, str(tmp_path / "park.shp"), st), key=lambda i: -i["rank"])
    assert [g["name"] for g in got] == ["大きな公園", "青葉7号公園"] and got[0]["rank"] == 43600 and got[0]["detail"]["公園種別"] == "近隣公園"
    assert abs(got[0]["lat"] - 33.59) < 0.001 and abs(got[0]["lon"] - 130.40) < 0.001      # 最大の区画の内側の点


def test_save_list_counts_and_adopt_keep_source_and_license(db, tmp_path):
    world(db, tmp_path)
    spec = bi.SPEC_BY_KEY["park"]
    rows = bi.candidates(db, spec, [it("bodik:park:x", "大きな公園", 33.592, 130.402, rank=43600.0, detail={"公園種別": "近隣公園"}),
                                    it("bodik:park:y", "小さな公園", 33.593, 130.404, rank=300.0)], {})
    assert bi.save(db, rows) == 2 and bi.save(db, rows) == 0                                  # 2回目は入らない
    c = rpc(db, "additions_counts")
    assert c["bodik"] == 2 and c["bodik_datasets"] == {"park": 2} and c["wikipedia"] == 0
    lst = rpc(db, "additions_list", p_source="bodik", p_dataset="park")
    assert [r["name"] for r in lst] == ["大きな公園", "小さな公園"]                              # 面積の大きい順
    assert lst[0]["detail"]["dataset_label"] == "都市公園（Shape）" and lst[0]["dataset"] == "park"
    assert rpc(db, "additions_list", p_source="bodik", p_dataset="inn") == []
    aid = lst[0]["id"]
    assert not rpc(db, "addition_decide", p_id=aid, p_decision="adopt", p_user="hirata")["ok"]            # 種別がない
    r = rpc(db, "addition_decide", p_id=aid, p_decision="adopt", p_user="hirata", p_name="大きな公園", p_category="park")
    assert r["ok"] and r["code"].startswith("FUK-")
    s = db.execute("select * from spots where id = %s", (r["spot_id"],)).fetchone()
    assert s["origin"] == "manual" and s["confidence"] == "unverified" and s["summary"] is None
    src = db.execute("select url, note from sources where spot_id = %s", (s["id"],)).fetchone()
    assert src["url"] == "https://data.bodik.jp/dataset/401307_tosikouen_shape" and "BODIK" in src["note"] and "CC BY 4.0" in src["note"]
    assert "目安" not in src["note"]                                                                  # 住所からの座標ではない
    # 住所からの座標の候補は、出典の注記に「目安」を残す
    g = bi.candidates(db, bi.SPEC_BY_KEY["inn"], [{"key": "bodik:inn:z", "name": "新しい旅館", "addr": "福岡市中央区天神三丁目８番１号", "lat": None,
                                                    "lon": None, "detail": {}, "rank": 0.0}], {},
                      session=FakeSession({"AddressSearch": [{"geometry": {"coordinates": [130.396, 33.5944]},
                                                              "properties": {"title": "福岡県福岡市中央区天神三丁目８番１号"}}]}), sleep=no_sleep)
    bi.save(db, g)
    gid = db.execute("select id from spot_additions where dataset = 'inn'").fetchone()["id"]
    r2 = rpc(db, "addition_decide", p_id=gid, p_decision="adopt", p_user="hirata", p_name="新しい旅館", p_category="commercial_facility")
    assert r2["ok"] and "目安" in db.execute("select note from sources where spot_id = %s", (r2["spot_id"],)).fetchone()["note"]
    # Wikipedia・Commons の候補は今までどおり（列を足しただけ）
    from locadb import addition_candidates as ac
    assert ac.save(db, [{"source": "wikipedia", "source_key": "wikipedia:1", "name": "駅前の広場", "lat": 33.59, "lon": 130.41, "area_code": None,
                         "suggest_cat": "park", "source_url": "https://ja.wikipedia.org/wiki/x", "files": None, "samples": [], "near_name": None,
                         "near_m": None}]) == 1
    w = rpc(db, "additions_list", p_source="wikipedia")[0]
    assert w["dataset"] is None and w["detail"] == {} and rpc(db, "additions_counts")["wikipedia"] == 1
