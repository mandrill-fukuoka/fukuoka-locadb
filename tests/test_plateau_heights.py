"""PLATEAU の建物の高さ：CityGML の読み取り・近傍の計算（DBなし）と、vibe への反映（DB。ci）"""
import math

import numpy as np

from locadb import plateau_heights as ph

GML = """<?xml version="1.0" encoding="UTF-8"?>
<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0" xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
  xmlns:gml="http://www.opengis.net/gml" xmlns:uro="http://www.kantei.go.jp/uro">
 <core:cityObjectMember><bldg:Building>
   <bldg:measuredHeight uom="m">52.3</bldg:measuredHeight>
   <bldg:lod0RoofEdge><gml:surfaceMember><gml:Polygon><gml:exterior><gml:LinearRing>
     <gml:posList>33.5900 130.4000 0 33.5900 130.4002 0 33.5902 130.4002 0 33.5902 130.4000 0</gml:posList>
   </gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember></bldg:lod0RoofEdge>
 </bldg:Building></core:cityObjectMember>
 <core:cityObjectMember><bldg:Building>
   <bldg:lod0RoofEdge><gml:posList>33.5910 130.4010 0 33.5910 130.4011 0</gml:posList></bldg:lod0RoofEdge>
 </bldg:Building></core:cityObjectMember>
 <core:cityObjectMember><bldg:Building>
   <bldg:measuredHeight uom="m">0</bldg:measuredHeight>
   <bldg:lod0RoofEdge><gml:posList>33.5920 130.4020 0 33.5920 130.4021 0</gml:posList></bldg:lod0RoofEdge>
 </bldg:Building></core:cityObjectMember>
 <core:cityObjectMember><bldg:Building>
   <bldg:measuredHeight uom="m">9.0</bldg:measuredHeight>
   <bldg:lod0RoofEdge><gml:posList>130.4030 33.5930 130.4031 33.5930</gml:posList></bldg:lod0RoofEdge>
 </bldg:Building></core:cityObjectMember>
</core:CityModel>"""


def test_parse_buildings_height_and_axis_order():
    b = ph.parse_buildings(GML.encode())
    assert len(b) == 2                                               # 高さなし・0m は捨てる
    lat, lon, h = b[0]
    assert abs(lat - 33.5901) < 1e-6 and abs(lon - 130.4001) < 1e-6 and h == 52.3
    lat, lon, h = b[1]                                               # 経度が先の座標も見分ける
    assert abs(lat - 33.5930) < 1e-6 and abs(lon - 130.40305) < 1e-6 and h == 9.0


def grid_of(bs):
    arr = np.array(bs)
    x, y = ph.to_xy(arr[:, 0], arr[:, 1])
    return ph.Grid(x, y, arr[:, 2])


def offset(dx_m, dy_m, lat=33.6, lon=130.4):
    return lat + dy_m / ph.M_LAT, lon + dx_m / ph.M_LON


def test_metrics_counts_heights_and_horizon():
    bs = [(*offset(50, 0), 60.0),            # 東50m・60m（高層）
          (*offset(0, 80), 20.0),            # 北80m・20m
          (*offset(0, 250), 50.0),           # 北250m・50m（高層・半径300m内）
          (*offset(400, 0), 90.0)]           # 300mの外
    g = grid_of(bs)
    m = ph.metrics(g, 33.6, 130.4)
    assert m["tall_300"] == 2 and m["h_max_100"] == 60.0 and abs(m["h_mean_100"] - 40.0) < 1e-6
    east = math.degrees(math.atan2(60 - ph.EYE_M, 50))               # 東の見上げ角が最大
    assert abs(m["horizon_max_deg"] - east) < 0.5
    assert 0 < m["horizon_mean_deg"] < m["horizon_max_deg"]
    assert ph.metrics(g, 33.7, 130.4) is None                       # 近くに建物がない地点は値なし


def test_self_building_not_in_horizon():
    g = grid_of([(*offset(1, 1), 100.0)])
    m = ph.metrics(g, 33.6, 130.4)
    assert m["horizon_max_deg"] == 0 and m["tall_300"] == 1


# ---- 以下は DB（ci のみ）----
from test_routes import add, spatial  # noqa: E402


def test_skyline_feeds_vibe(db):
    sid = add(db, "オフィス街の通り", "alley", 0, 0)
    spatial(db, sid, road_width_m=3.0, building_cover_100=0.05)
    other = add(db, "郊外の通り", "alley", 20000, 0)
    spatial(db, other, road_width_m=3.0, building_cover_100=0.05)
    db.execute("insert into spot_skyline (spot_id, h_max_100, h_mean_100, tall_300, horizon_mean_deg, horizon_max_deg, source) "
               "values (%s, 80, 35, 12, 18, 40, 'test')", (sid,))
    db.commit()
    db.execute("select vibe_apply()")
    db.commit()
    sig = lambda i: db.execute("select tag_signal(%s, 'skyline') v", [i]).fetchone()["v"]
    assert sig(sid) >= 0.79 and sig(other) < 0.1                    # 高層が多い地点は skyline が高い（建物の被覆が低くても）
    line = db.execute("select vibe_line(%s) v", [sid]).fetchone()["v"]
    assert "周り300mに高さ45m以上のビル12棟" in line
    assert "ビル" not in db.execute("select vibe_line(%s) v", [other]).fetchone()["v"]
