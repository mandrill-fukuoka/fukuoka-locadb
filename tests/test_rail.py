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
