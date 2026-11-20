"""場所を探す：計算済みの距離での絞り込み（騒音・トイレ・駐車場・ドローン）と、雰囲気の複数指定（順3、2026-11-20）"""
from test_routes import add


def search(db, **kw):
    args = ", ".join(f"{k} => %s" for k in kw)
    return db.execute(f"select search_spots({args}) as r", list(kw.values())).fetchone()["r"]


def names(rows):
    return sorted(r["name"] for r in rows)


def sp(db, sid, **cols):
    keys = ", ".join(["spot_id", "spatial_version", "confidence", *cols])
    ph = ", ".join(["%s"] * (3 + len(cols)))
    db.execute(f"insert into spot_spatial ({keys}) values ({ph})", [sid, "spatial-3", "ok", *cols.values()])
    db.commit()


def make(db):
    a = add(db, "静岸", "coast", 0, 0)      # 静か・トイレ近い・駐車場近い
    b = add(db, "線岸", "coast", 300, 0)    # 線路が近い
    c = add(db, "空岸", "coast", 600, 0)    # 滑走路が近い
    d = add(db, "未岸", "coast", 900, 0)    # 計算前
    sp(db, a, rail_m=500, major_road_m=400, runway_m=9000, toilet_m=80, parking_m=200)
    sp(db, b, rail_m=40, major_road_m=400, runway_m=9000, toilet_m=400, parking_m=900)
    sp(db, c, rail_m=500, major_road_m=400, runway_m=1200, toilet_m=250, parking_m=250)
    return a, b, c, d


def test_quiet_toilet_and_parking_filters(db):
    make(db)
    assert names(search(db, p_category="coast")) == ["未岸", "空岸", "線岸", "静岸"]
    assert names(search(db, p_category="coast", p_quiet="quiet")) == ["静岸"]       # 線路150m・滑走路2500mの内側は除く。値のない地点も除く
    assert names(search(db, p_category="coast", p_toilet_m=100)) == ["静岸"]
    assert names(search(db, p_category="coast", p_toilet_m=300)) == ["空岸", "静岸"]
    assert names(search(db, p_category="coast", p_parking_m=300)) == ["空岸", "静岸"]
    assert names(search(db, p_category="coast", p_quiet="quiet", p_parking_m=300, p_toilet_m=300)) == ["静岸"]   # 条件はすべてに当てはまるもの


def test_drone_filter_needs_did_and_far_from_airport(db):
    a, b, c, d = make(db)
    assert search(db, p_category="coast", p_drone="easy") == []                  # 人口集中地区のデータがなければ出さない（空港から遠くても）
    db.execute("""insert into did_areas (name, pop, geom) values
        ('テスト市', 1000, ST_Multi(ST_GeomFromText('POLYGON((130.0 33.0,130.001 33.0,130.001 33.001,130.0 33.001,130.0 33.0))', 4326))::geography)""")
    db.commit()
    assert names(search(db, p_category="coast", p_drone="easy")) == ["線岸", "静岸"]   # 空港から6km以上。計算前の地点は除く
    lon, lat = db.execute("select ST_X(geom::geometry) x, ST_Y(geom::geometry) y from spots where id = %s", (a,)).fetchone().values()
    db.execute("delete from did_areas")
    db.execute("""insert into did_areas (name, pop, geom) values ('近傍', 1000,
        ST_Multi(ST_Buffer(ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, 100)::geometry)::geography)""", (lon, lat))
    db.commit()
    assert names(search(db, p_category="coast", p_drone="easy")) == ["線岸"]           # 静岸は人口集中地区の中


def test_tags_any_or_all(db):
    a = add(db, "甲", "coast", 0, 0)
    b = add(db, "乙", "coast", 300, 0)
    c = add(db, "丙", "coast", 600, 0)
    for sid, tag in ((a, "lonely"), (a, "nostalgic"), (b, "lonely"), (c, "nostalgic")):
        db.execute("insert into spot_tags (spot_id, tag_code, origin, score) values (%s,%s,'human',1)", (sid, tag))
    db.commit()
    assert names(search(db, p_tags=["lonely", "nostalgic"])) == ["丙", "乙", "甲"]                  # 既定：どれか1つ
    assert names(search(db, p_tags=["lonely", "nostalgic"], p_tags_all=True)) == ["甲"]            # すべて
    assert names(search(db, p_tags=["lonely"], p_tags_all=True)) == ["乙", "甲"]
