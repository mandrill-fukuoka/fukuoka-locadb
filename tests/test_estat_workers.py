import pytest

from locadb import estat_workers as ew


def test_mesh_center_of_500m_codes():
    lon, lat = ew.mesh_center("503000002")                # 5030 の南西端の 500m セルのうち南東（2）
    assert abs(lat - (33 + 20 / 60 + 7.5 / 3600)) < 1e-9
    assert abs(lon - (130 + 22.5 / 3600 + 11.25 / 3600)) < 1e-9
    # 同じ 1km セルの4つの 500m セルは、南西→南東→北西→北東で中心がずれる
    sw, se, nw, ne = (ew.mesh_center(f"50305511{q}") for q in "1234")
    assert se[0] > sw[0] and se[1] == sw[1] and nw[1] > sw[1] and nw[0] == sw[0] and ne == (se[0], nw[1])
    with pytest.raises(ValueError):
        ew.mesh_center("50300000")                        # 8桁（1km）は対象外
    with pytest.raises(ValueError):
        ew.mesh_center("503000005")                       # 最後の桁は 1〜4


def test_mesh_center_lands_in_fukuoka_city():
    # 天神（33.591, 130.399）を含む 500m セルの中心は、天神から 500m 以内
    lat, lon = 33.591, 130.399
    p = int(lat * 1.5); u = int(lon - 100)
    r = int((lat - p / 1.5) / (5 / 60)); s = int((lon - 100 - u) / (7.5 / 60))
    t = int(((lat - p / 1.5) - r * 5 / 60) / (30 / 3600)); v = int(((lon - 100 - u) - s * 7.5 / 60) / (45 / 3600))
    north = ((lat - p / 1.5) - r * 5 / 60 - t * 30 / 3600) >= 15 / 3600          # 1kmセルの北半分か
    east = ((lon - 100 - u) - s * 7.5 / 60 - v * 45 / 3600) >= 22.5 / 3600        # 東半分か
    code = f"{p}{u:02d}{r}{s}{t}{v}{1 + east + 2 * north}"
    clon, clat = ew.mesh_center(code)
    assert abs(clat - lat) <= 7.5 / 3600 + 1e-9 and abs(clon - lon) <= 11.25 / 3600 + 1e-9    # セルの半分の大きさ以内


def meta(classes):
    return {"GET_META_INFO": {"METADATA_INF": {"CLASS_INF": {"CLASS_OBJ": [
        {"@id": "area", "CLASS": [{"@code": "x", "@name": "x"}]}, {"@id": "cat01", "CLASS": classes}]}}}}


def test_workers_code_picks_total_workers():
    cl = [{"@code": "0010", "@name": "事業所数＿Ａ～Ｒ全産業（Ｓ公務を除く）"},
          {"@code": "0210", "@name": "従業者数＿Ａ～Ｒ全産業（Ｓ公務を除く）"},
          {"@code": "0220", "@name": "従業者数＿Ｃ～Ｅ第２次産業"}]
    assert ew.workers_code(meta(cl)) == "0210"
    with pytest.raises(RuntimeError):
        ew.workers_code(meta(cl[:1]))


def test_parse_skips_secret_values_and_outside_bbox():
    vals = [{"@area": "503000002", "$": "5"},                       # 福岡市の範囲の外（5030 の南西端）
            {"@area": "503042011", "$": "120"}, {"@area": "503042011", "$": "X"}, {"@area": "503042012", "$": "-"},
            {"@area": "50304201", "$": "9"}]
    rows = ew.parse([{"@area": "503042011", "$": "120"}, {"@area": "503042012", "$": "X"},
                     {"@area": "503042013", "$": "-"}, {"@area": "50304201", "$": "9"}, vals[0]])
    assert [r["mesh_code"] for r in rows] == ["503042011"] and rows[0]["workers"] == 120


# ---- DB（ci）
from test_routes import add, at, spatial  # noqa: E402


def mesh(db, code, workers, dx, dy):
    lon, lat = at(dx, dy)
    db.execute("insert into workplace_mesh (mesh_code, workers, survey_year, geom, source) values "
               "(%s,%s,2016, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography,'e-Stat test')", (code, workers, lon, lat))
    db.commit()


def test_daytime_refresh_weights_by_distance(db):
    near = add(db, "オフィス街の通り", "alley", 0, 0)
    mid = add(db, "400m先の通り", "alley", 400, 0)
    far = add(db, "遠い通り", "alley", 5000, 0)
    for s in (near, mid, far):
        spatial(db, s, road_width_m=3.0)
    assert db.execute("select daytime_refresh() n").fetchone()["n"] == 0                   # メッシュがなければ何もしない
    mesh(db, "m1", 40000, 0, 0)
    assert db.execute("select daytime_refresh() n").fetchone()["n"] == 3
    d = {r["spot_id"]: r["workers_800"] for r in db.execute("select * from spot_daytime").fetchall()}
    assert d[near] == 40000 and 19000 <= d[mid] <= 21000 and d[far] == 0                   # 400m で重みは半分
    st = db.execute("select daytime_stats() d").fetchone()["d"]
    assert st["meshes"] == 1 and st["workers_total"] == 40000 and st["spots_with_workers_800"] == 2


def test_daytime_feeds_vibe_and_line(db):
    a = add(db, "オフィス街の通り", "alley", 0, 0)
    b = add(db, "住宅街の通り", "alley", 3000, 0)
    for s in (a, b):
        spatial(db, s, road_width_m=3.0)
    db.execute("update spot_spatial set shops_150 = 3, bars_150 = 0, eateries_150 = 1")
    db.commit()
    before = {r["spot_id"]: r["signals"] for r in db.execute("select spot_id, vibe_signals(spot_id) signals from spot_spatial").fetchall()}
    mesh(db, "m1", 60000, 0, 0)
    db.execute("select daytime_refresh()")
    db.commit()
    after = {r["spot_id"]: r["signals"] for r in db.execute("select spot_id, vibe_signals(spot_id) signals from spot_spatial").fetchall()}
    assert float(after[a]["high_traffic"]) > float(before[a]["high_traffic"])
    assert after[b] == before[b]                                                             # 遠い地点は変わらない
    assert "周り800mの従業者 約6.0万人（2016年）" in db.execute("select vibe_line(%s) l", [a]).fetchone()["l"]
    assert "従業者" not in db.execute("select vibe_line(%s) l", [b]).fetchone()["l"]
