"""回帰テスト：ドローンの目安（人口集中地区）と潮位（DB変更204）。外部へは出ない"""
import datetime
import json

from tools.layer1_import import parse_tide_line
from osmworld import World, spots

LINE = " 37 19 15 24 44 71102129144143130109 90 78 77 87105127149167174164137 9926 1 1QF 829145195617499999999999999 150 151334 7699999999999999"


def test_parse_tide_line():
    day, highs, lows = parse_tide_line(LINE)
    assert day == "2026-01-01"
    assert highs == [{"t": "08:29", "cm": 145}, {"t": "19:56", "cm": 174}]      # 欠測（9999・999）は除く
    assert lows == [{"t": "01:50", "cm": 15}, {"t": "13:34", "cm": 76}]
    assert parse_tide_line("short") is None


def rpc(db, fn, *a):
    row = db.execute(f"select {fn}({', '.join(['%s'] * len(a))}) r", a).fetchone()
    db.commit()
    return row["r"]


def test_drone_and_tide_views(db, tmp_path):
    w = World()
    w.point(10, 130.39, 33.585, amenity="place_of_worship", name="警固神社")
    w.point(11, 130.40, 33.590, natural="beach", name="砂浜A")
    w.run(db, tmp_path)
    by = {s["name"]: s for s in spots(db)}
    temple, beach = by["警固神社"]["id"], by["砂浜A"]["id"]
    assert rpc(db, "drone_view", temple) is None                              # 取り込み前は何も出さない
    db.execute("""insert into did_areas (name, pop, geom) values
        ('テスト市', 1000, ST_Multi(ST_GeomFromText('POLYGON((130.385 33.580,130.395 33.580,130.395 33.590,130.385 33.590,130.385 33.580))', 4326))::geography)""")
    db.execute("insert into spot_spatial (spot_id, spatial_version, confidence, runway_m) values (%s,'spatial-3','ok',3000)", (temple,))
    db.commit()
    d = rpc(db, "drone_view", temple)
    assert d["did"] is True and d["airport_near"] is True
    assert rpc(db, "drone_view", beach)["did"] is False                       # 区域の外
    assert rpc(db, "drone_view", beach)["airport_near"] is None               # 空間特徴が未計算
    today = db.execute("select (now() at time zone 'Asia/Tokyo')::date d").fetchone()["d"]
    for i in (0, 1, 2):
        db.execute("insert into tide_daily (day, highs, lows) values (%s, %s::jsonb, '[]'::jsonb)",
                   (today + datetime.timedelta(days=i), json.dumps([{"t": "08:29", "cm": 145 + i}])))
    db.commit()
    t = rpc(db, "tide_view", beach)
    assert [x["highs"][0]["cm"] for x in t["days"]] == [145, 146]             # 今日と明日だけ
    assert rpc(db, "tide_view", temple) is None                               # 海辺でない地点には出さない
    det = rpc(db, "spot_detail", beach)
    assert det["tide"]["days"] and det["drone"]["did"] is False
