"""ローカルPostGISでの結合テスト（OSM・Claude APIは使わずテストデータで実行）"""
import json
from locadb import build_clusters, merge, search_scene
from locadb.db import connect

T = [  # 概略座標のテストデータ（実在確認用ではない）
 ("t1","春吉の路地","alley",33.5882,130.4035),("t2","那珂川","river",33.5868,130.4062),
 ("t3","中洲の飲食店街","eatery_street",33.5920,130.4062),("t4","天神の新築ビル","new_building",33.5912,130.3990),
 ("t5","長浜の倉庫","warehouse",33.5968,130.3878),("t6","博多港ふ頭","port",33.6000,130.3885),
 ("t7","警固神社","shrine_temple",33.5893,130.3972),("t8","舞鶴公園","park",33.5852,130.3832),
 ("t9","赤煉瓦文化館","heritage",33.5928,130.4012),("t10","春吉の路地","alley",33.58825,130.40355),
 ("t11","那の津の海岸","coast",33.5990,130.3920),("t12","清川の住宅街","residential",33.5835,130.4030),
]
ERA = {"春吉の路地":"showa","天神の新築ビル":"reiwa","中洲の飲食店街":"heisei","長浜の倉庫":"showa","警固神社":"historic","赤煉瓦文化館":"historic"}
TAGS = {"春吉の路地":{"lonely":0.7,"night_ok":0.8,"narrow_street":0.9},"那珂川":{"river_view":1,"night_ok":0.9,"lonely":0.6},
        "長浜の倉庫":{"lonely":0.8,"inorganic":0.8,"night_ok":0.6},"博多港ふ頭":{"sea_view":1,"lonely":0.7,"industrial_structure":0.9},
        "那の津の海岸":{"sea_view":1,"lonely":0.6},"天神の新築ビル":{"skyline":0.9,"reiwa":1}}

def setup():
    with connect() as c:
        c.execute("truncate raw_candidates, spots, clusters, change_risks, sources, job_runs restart identity cascade")
        for ref,n,cat,lat,lon in T:
            c.execute("""insert into raw_candidates(source,source_ref,name,geom,raw,category_hint)
              values('test',%s,%s,ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography,%s,%s)""",
              (ref,n,lon,lat,json.dumps({"area":"fuk-chuo"}),cat))
        c.commit()

def fake_classify():
    with connect() as c:
        for n,e in ERA.items(): c.execute("update spots set era_feel=%s where name=%s",(e,n))
        for n,tg in TAGS.items():
            for code,s in tg.items():
                c.execute("insert into spot_tags select id,%s,'ai',%s from spots where name=%s",(code,s,n))
        c.execute("update spots set status='ai_tagged', confidence='ai_estimated'")
        c.commit()

setup()
st = merge.run("fuk-chuo"); assert st["created"] == 11 and st["merged"] == 1, st
fake_classify()
st = build_clusters.run("fuk-chuo"); assert st["clusters"] > 0
with connect() as c:
    for r in c.execute("select * from v_clusters limit 6"): print(" ", r["travel_mode"], r["total_minutes"], r["transition_score"], r["route"])

search_scene.interpret = lambda conn, scene: {"sequence":["commercial","old_town","water"],
    "tags":{"lonely":1.0,"night_ok":0.8,"sea_view":0.6},"travel_mode":"any","notes":"(テスト用の固定解釈)"}
out = search_scene.search("深夜、一人で歩く。都会だが少し寂しい。海が見える")
for r in out["results"][:3]: print("  検索:", r["match"], " → ".join(s["name"] for s in r["spots"]))
print("PASS")
