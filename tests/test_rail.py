"""線路沿い（DB変更211）・工場倉庫に入れない名前・説明文の根拠のない記述（2026-10-29）"""
from locadb.categories import categories_of
from locadb.verify import check_summary
from osmworld import World, spot, spots
from test_verify import S, _places, checks, note


def test_rail_form_and_group(db, tmp_path):
    w = World()
    # 中央区を東西に走る線路：西半分は地上、東半分は高架
    w.line(10, [(130.381, 33.590), (130.400, 33.590)], railway="rail", name="JR筑肥線")
    w.line(11, [(130.400, 33.590), (130.419, 33.590)], railway="rail", name="JR筑肥線", bridge="viaduct", layer="1")
    w.point(20, 130.390, 33.592, railway="level_crossing", name="大濠踏切")
    w.run(db, tmp_path)
    rows = spots(db, "category = 'railway' and data_status <> 'archived'")
    assert rows and {r["rail_form"] for r in rows} == {"ground", "elevated"}
    west = min(rows, key=lambda r: db.execute("select ST_X(geom::geometry) x from spots where id=%s", [r["id"]]).fetchone()["x"])
    assert west["rail_form"] == "ground"
    grp = {r["code"]: r["landscape_group"] for r in db.execute(
        "select code, landscape_group from spot_categories where code in ('railway','level_crossing','station')")}
    assert grp == {"railway": "rail", "level_crossing": "rail", "station": "transport"}
    assert db.execute("select grp_match('transport', 'rail') v").fetchone()["v"] is False




def test_spot_label_sql(db):
    q = lambda *a: db.execute("select spot_label(%s, %s, %s, %s) v", list(a)).fetchone()["v"]
    assert q("JR鹿児島本線", "railway", "福岡市東区箱崎一丁目", "elevated") == "JR鹿児島本線（箱崎一丁目付近・高架）"
    assert q("那珂川", "river", "福岡市博多区中洲五丁目", None) == "那珂川（中洲五丁目付近）"
    assert q("JR筑肥線", "railway", None, "ground") == "JR筑肥線（地上）"
    assert q("博多駅", "station", "福岡市博多区博多駅中央街", None) == "博多駅"
    assert q("（無名）海岸・護岸・干潟（香住ヶ丘七丁目）", "coast", "福岡市東区香住ヶ丘七丁目", None) == "（無名）海岸・護岸・干潟（香住ヶ丘七丁目）"
    assert q("玄界島の海岸", "coast", "福岡市西区大字玄界島", None) == "玄界島の海岸（玄界島付近）"


def test_not_industrial_names():
    ind = lambda nm: categories_of({"landuse": "industrial", "name": nm}, "w", True)
    assert ind("西鉄バス 営業所") == []
    assert ind("新明和工業株式会社九州営業所") == []
    assert ind("乗馬クラブ ルヴァード花畑") == []
    assert ind("九州電力 社員研修所") == []
    assert ind("高砂倉庫 那ノ津営業所") == ["factory"]       # 倉庫の会社の営業所は残す
    assert ind("橋本車両基地") == ["factory"]
    assert ind("パナソニック福岡工場") == ["factory"]
    assert ind("日本通運 福岡海運支店") == ["factory"]          # 物流の拠点は残す
    assert ind("九州産直クラブ") == ["factory"]
    assert categories_of({"building": "warehouse", "name": "○○運輸 福岡営業所"}, "w", True) == ["warehouse"]


def test_unsupported_summary():
    s = S(name="今泉公園", lat=33.5880, lon=130.3990)
    p = _places()
    ok = "今泉公園は中央区の住宅地にある小さな公園。天神中央公園まで約450m。ベンチと植え込みがあり、周りは中低層の建物に囲まれている。"
    assert "sum_unsupported" not in checks(check_summary(s, note(ok), p))
    bad = ok + "日中は多くの通勤客や観光客で賑わい、夜間も人通りは比較的多い。"
    assert "sum_unsupported" in checks(check_summary(s, note(bad), p))
    assert "sum_unsupported" in checks(check_summary(s, {**note(bad), "note_id": None, "source": "published"}, p))


def test_keywords_rail_and_station():
    from test_routes import php_interpret
    assert php_interpret("高架下の線路沿いを歩き、踏切を渡る")["sequence"] == ["rail"]
    assert php_interpret("団地から公園を抜けて駅へ向かう")["sequence"] == ["residential", "green", "transport"]
    assert php_interpret("川沿いから線路沿いへ")["sequence"] == ["river", "rail"]


def test_dry_run_with_railway(db, tmp_path, monkeypatch):
    """坂道の一覧（slopes.csv）を書く dry_run が、線路の形の attrs で止まらない（2026-10-29 の不具合）"""
    from locadb import pipeline
    monkeypatch.setenv("SLOPES_CSV", str(tmp_path / "slopes.csv"))
    w = World()
    w.line(10, [(130.381, 33.590), (130.419, 33.590)], railway="rail", name="JR筑肥線")
    p = w.write(tmp_path / "a.osm")
    snap, meta = pipeline.get_snapshot(str(p), set(), True)
    assert any(s.attrs and "rail_form" in s.attrs for s in snap.samples)


def test_keywords_stage3_scenes():
    from test_routes import php_interpret
    q = php_interpret("自転車で坂道を下り、踏切を渡って商店街に入る")
    assert q["sequence"] == ["slope", "rail", "old_town"] and q["travel_mode"] == "walk"   # 自転車は車にしない
    assert php_interpret("刑事が車で港湾道路を流しながら、コンテナヤードを見回る")["sequence"] == ["port_industrial"]
    assert php_interpret("再開発の工事現場の前を通り、まだ残る古い路地に入っていく")["sequence"] == ["vacant", "old_town"]
    assert php_interpret("放課後、高校から川沿いの土手を二人で歩いて帰る")["sequence"] == ["institution", "river"]
    assert php_interpret("島へ渡るフェリー乗り場から、漁港の路地を歩いて丘の上へ")["sequence"] == ["port_industrial", "sea", "old_town", "green"]


# ---------- 2026-10-29 A・B：足りない地点 ----------
def test_gap_categories_rules():
    c = categories_of
    assert c({"historic": "castle", "name": "福岡城跡"}, "w", True)[0] == "castle"
    assert c({"man_made": "breakwater"}, "w", False) == ["breakwater"]
    assert c({"man_made": "lighthouse"}, "n", False) == ["lighthouse"]
    assert c({"amenity": "ferry_terminal", "name": "能古渡船場"}, "n", False) == ["harbour_structure"]
    assert c({"highway": "motorway", "bridge": "yes", "name": "福岡都市高速環状線"}, "w", False) == ["expressway"]
    assert c({"highway": "motorway", "tunnel": "yes", "name": "福岡都市高速環状線"}, "w", False) == ["underpass"]
    assert c({"highway": "pedestrian", "name": "柳橋連合市場"}, "w", False) == ["eatery_street"]
    assert c({"name": "藤崎商店街デンタルクリニック", "amenity": "dentist"}, "n", False) == []
    assert c({"name": "みのしま連合商店街振興組合事務所"}, "n", False) == []
    assert c({"name": "箱崎商店街", "landuse": "retail"}, "w", True) == ["shotengai"]
    assert c({"leisure": "park", "name": "室見川河畔公園"}, "w", True)[0] == "riverbank"
    assert c({"building": "ruins"}, "w", True) == ["ruins"]
    assert c({"disused:shop": "convenience", "building": "yes", "disused": "yes", "name": "旧コンビニ"}, "w", True) == []


def test_gap_samples(db, tmp_path):
    w = World()
    # 中洲（中央区）にバー15軒、屋台4軒。海岸と、海岸のそばの駐車場・離れた駐車場
    for i in range(15):
        w.point(100 + i, 130.405 + (i % 5) * 0.0003, 33.592 + (i // 5) * 0.0003, amenity="bar", name=f"バー{i}")
    for i in range(4):
        w.point(200 + i, 130.395 + i * 0.0002, 33.590, amenity="fast_food", name=f"屋台{i}ちゃん")
    w.line(300, [(130.381, 33.5985), (130.419, 33.5985)], natural="coastline", name="博多湾")
    w.area(310, 130.385, 33.5978, size=0.0003, amenity="parking", parking="surface")
    w.area(312, 130.3856, 33.5980, size=0.0001, amenity="parking", parking="surface")    # 同じ400m四方の小さい駐車場は取らない
    w.area(311, 130.385, 33.583, size=0.0003, amenity="parking", parking="surface")
    w.line(320, [(130.382, 33.585), (130.418, 33.585)], highway="motorway", bridge="yes", layer="1", name="福岡都市高速環状線")
    w.line(330, [(130.383, 33.5975), (130.386, 33.5980)], man_made="breakwater")
    w.run(db, tmp_path)
    cats = lambda c: spots(db, "category = %s and data_status <> 'archived'", c)
    assert len(cats("nightlife_area")) == 1 and len(cats("yatai_area")) == 1
    assert len(cats("seaside_parking")) == 1                 # 海から離れた駐車場・同じ四方の小さい駐車場は取らない
    assert cats("expressway") and cats("breakwater")
    n = cats("nightlife_area")[0]
    assert n["name"].startswith("（無名）飲み屋街")
    # 2回目も同じ地点（キーが変わらない）
    w.run(db, tmp_path)
    assert [r["id"] for r in cats("nightlife_area")] == [n["id"]]


# ---------- 2026-10-30 地点リスト ----------
def test_farmland_and_terminal_rules():
    assert categories_of({"landuse": "farmland"}, "w", True) == ["farmland"]
    assert categories_of({"landuse": "farmland"}, "n", False) == []
    assert categories_of({"aeroway": "terminal", "name": "福岡空港国内線ターミナル"}, "w", True)[0] == "station"


def test_farmland_size_and_spacing(db, tmp_path):
    w = World()
    w.area(400, 130.39, 33.585, size=0.0008, landuse="farmland")      # 約1.4ha 以上
    w.area(401, 130.3905, 33.5855, size=0.0003, landuse="farmland")   # 同じ800m四方の小さい農地
    w.area(402, 130.41, 33.595, size=0.0002, landuse="farmland")      # 小さすぎる
    w.run(db, tmp_path)
    assert len(spots(db, "category = 'farmland' and data_status <> 'archived'")) == 1


def test_curated_resolve(db):
    from shapely.geometry import LineString, Point, Polygon
    from tools.curated_import import resolve

    class FakeH:
        geom = {"n1": Point(130.40, 33.59), "w2": Polygon([(130.40, 33.59), (130.41, 33.59), (130.41, 33.60), (130.40, 33.60)]),
                "w3": LineString([(130.40, 33.59), (130.42, 33.59)])}
        named = {"大博通り": [LineString([(130.40, 33.58), (130.40, 33.60)])]}
    db.execute("insert into spots (name, category, geom) values ('博多駅', 'station', ST_SetSRID(ST_MakePoint(130.41, 33.595),4326)::geography)")
    db.commit()
    h = FakeH()
    assert resolve({"osm": "n1", "place": "node"}, h, db).x == 130.40
    assert Polygon(h.geom["w2"]).contains(resolve({"osm": "w2", "place": "inside"}, h, db))
    assert abs(resolve({"osm": "w3", "place": "middle"}, h, db).x - 130.41) < 1e-9
    p = resolve({"place": "nearest_to_spot", "ways_named": "大博通り", "spot": "博多駅"}, h, db)
    assert abs(p.y - 33.595) < 1e-9 and abs(p.x - 130.40) < 1e-9
    assert resolve({"osm": "w9", "place": "node"}, h, db) is None


def test_curated_crossing_and_footbridges(db, monkeypatch):
    from shapely.geometry import LineString
    from tools import curated_import as ci
    monkeypatch.setattr(ci, "town_at", lambda p: "天神一丁目")

    class FakeH:
        named = {"国体道路": [LineString([(130.390, 33.585), (130.400, 33.585)])],
                 "警固本通り": [LineString([(130.393, 33.580), (130.393, 33.590)])],
                 "明治通り": [LineString([(130.390, 33.592), (130.410, 33.592)])]}
        footbridges = [LineString([(130.395, 33.5918), (130.395, 33.5922)]),      # 明治通りを渡る
                       LineString([(130.3951, 33.5917), (130.3951, 33.5923)]),    # 同じ場所（80m以内）は1つ
                       LineString([(130.405, 33.580), (130.405, 33.581)])]        # 渡らない
        narrow, geom = [], {}
    p = ci.resolve({"place": "road_crossing", "roads_a": ["国体道路"], "roads_b": ["警固本通り"]}, FakeH(), db)
    assert (round(p.x, 3), round(p.y, 3)) == (130.393, 33.585)
    assert ci.resolve({"place": "road_crossing", "roads_a": ["国体道路"], "roads_b": ["天神西通り"]}, FakeH(), db) is None
    res = ci.resolve_many({"place": "footbridges_over", "roads": ["明治通り", "渡辺通り"]}, FakeH(), db)
    assert [n for n, _, _ in res] == ["明治通りの歩道橋（天神一丁目）"]


def test_curated_user_point(db):
    from tools.curated_import import resolve
    p = resolve({"place": "point", "lat": 33.585249, "lon": 130.391518}, None, db)
    assert (p.y, p.x) == (33.585249, 130.391518)
