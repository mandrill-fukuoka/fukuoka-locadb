"""駅の人の多さ：S12 の読み取り（DBなし）と、地点ごとの数え直し・vibe への反映（DB。ci）"""
import pytest

from locadb import station_flow as sf


def feat(name, op, line, code, lon, lat, pax22=None, dup22=1, pax19=None, dup19=1):
    p = {"S12_001": name, "S12_001c": code, "S12_001g": code, "S12_002": op, "S12_003": line,
         "S12_050": dup22, "S12_053": pax22, "S12_038": dup19, "S12_041": pax19, "S12_046": 1, "S12_049": None}
    return {"geometry": {"type": "LineString", "coordinates": [[lon, lat], [lon + 0.0002, lat]]}, "properties": p}


def test_group_sums_operators_and_skips_duplicates_and_outside():
    feats = [
        feat("博多", "九州旅客鉄道", "鹿児島線", "g1", 130.42, 33.59, 200000),
        feat("博多", "福岡市", "空港線", "g1", 130.4205, 33.5902, 130000),
        feat("博多", "福岡市", "七隈線", "g1", 130.421, 33.59, 0, dup22=2),            # 重複の行は数えない
        feat("小さな駅", "西日本鉄道", "A線", "g2", 130.30, 33.55, None, pax19=900),     # 2022 が空なら 2019 を使う
        feat("遠い駅", "X", "Y線", "g3", 131.50, 33.55, 5000),                            # 範囲の外
        feat("空の駅", "X", "Y線", "g4", 130.40, 33.55, None, pax19=None),              # どの年度も値がない
    ]
    rows = {r["name"]: r for r in sf.group(feats)}
    assert set(rows) == {"博多", "小さな駅"}
    assert rows["博多"]["pax"] == 330000 and rows["博多"]["operators"] == ["九州旅客鉄道", "福岡市"] and rows["博多"]["fiscal_year"] == 2022
    assert rows["小さな駅"]["pax"] == 900 and rows["小さな駅"]["fiscal_year"] == 2019
    assert 130.42 < rows["博多"]["lon"] < 130.421                                        # 線の代表点は頂点の平均
    assert sf.group(feats)[0]["name"] == "博多"                                           # 乗降客数の多い順


def test_point_of_handles_point_and_nested_lines():
    assert sf.point_of({"type": "Point", "coordinates": [130.4, 33.5]}) == (130.4, 33.5)
    assert sf.point_of({"type": "MultiLineString", "coordinates": [[[130.0, 33.0], [130.2, 33.2]]]}) == (130.1, 33.1)


# ---- DB（ci）
from test_routes import add, at, spatial  # noqa: E402


def station(db, code, name, pax, dx, dy):
    lon, lat = at(dx, dy)
    db.execute("insert into station_flow (group_code, name, operators, pax, fiscal_year, geom, source) values "
               "(%s,%s,'{x}',%s,2022, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography,'S12-23')", (code, name, pax, lon, lat))
    db.commit()


def test_footfall_refresh_weights_by_distance(db):
    near = add(db, "駅前の通り", "alley", 0, 0)
    mid = add(db, "駅から400mの通り", "alley", 400, 0)
    far = add(db, "遠い通り", "alley", 5000, 0)
    for s in (near, mid, far):
        spatial(db, s, road_width_m=3.0)
    assert db.execute("select footfall_refresh() n").fetchone()["n"] == 0                  # 駅がなければ何もしない
    station(db, "g1", "博多", 100000, 0, 0)
    assert db.execute("select footfall_refresh() n").fetchone()["n"] == 3
    f = {r["spot_id"]: r for r in db.execute("select * from spot_footfall").fetchall()}
    assert f[near]["flow_800"] == 100000 and f[near]["station_m"] == 0 and f[near]["station_name"] == "博多"
    assert 49000 <= f[mid]["flow_800"] <= 51000 and 395 <= f[mid]["station_m"] <= 405        # 400m で重みは半分
    assert f[far]["flow_800"] == 0 and f[far]["station_m"] is None and f[far]["station_name"] is None
    st = db.execute("select footfall_stats() d").fetchone()["d"]
    assert st["stations"] == 1 and st["spots"] == 3 and st["spots_with_station_800"] == 2


def test_station_flow_feeds_vibe_and_line(db):
    a = add(db, "博多駅前の通り", "alley", 0, 0)
    b = add(db, "住宅街の通り", "alley", 3000, 0)
    for s in (a, b):
        spatial(db, s, road_width_m=3.0)
    db.execute("update spot_spatial set shops_150 = 3, bars_150 = 0, eateries_150 = 1")      # 店は少ない（同じ条件）
    db.commit()
    before = {r["spot_id"]: r["signals"] for r in db.execute("select spot_id, vibe_signals(spot_id) signals from spot_spatial").fetchall()}
    station(db, "g1", "博多", 200000, 0, 0)
    db.execute("select footfall_refresh()")
    db.commit()
    after = {r["spot_id"]: r["signals"] for r in db.execute("select spot_id, vibe_signals(spot_id) signals from spot_spatial").fetchall()}
    assert float(after[a]["high_traffic"]) > float(before[a]["high_traffic"])                # 駅が近いと人通りが多い側に
    assert float(after[a]["low_traffic"]) < float(before[a]["low_traffic"])                  # 少ない側は下がる
    assert after[b] == before[b]                                                             # 駅が遠い地点は変わらない
    line = db.execute("select vibe_line(%s) l", [a]).fetchone()["l"]
    assert "最寄り駅博多（1日の乗降 約20.0万人）まで0m" in line
    assert "最寄り駅" not in db.execute("select vibe_line(%s) l", [b]).fetchone()["l"]
