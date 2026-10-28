"""流れで探す（build_routes）の回帰テスト（2026-10-28 流れ検索の精度改善 段階1・2）

架空の地点を直接入れて、組み合わせ・点数・並びの規則を確かめる。
"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAT0, LON0 = 33.590, 130.400


def at(dx, dy):
    """基準点から東へ dx m・北へ dy m の位置（lon, lat）"""
    return LON0 + dx / 92470.0, LAT0 + dy / 111000.0


def add(db, name, cat, dx, dy, **kw):
    lon, lat = at(dx, dy)
    cols = {"code": kw.pop("code", None), "name": name, "category": cat}
    cols.update(kw)
    keys = ", ".join(cols)
    ph = ", ".join(["%s"] * len(cols))
    r = db.execute(f"insert into spots ({keys}, geom) values ({ph}, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) returning id",
                   [*cols.values(), lon, lat]).fetchone()
    db.commit()
    return r["id"]


def routes(db, seq, tags=None, mode="walk", start=None, limit=8):
    r = db.execute("select build_routes(%s, %s::jsonb, %s, null, %s, %s) as r",
                   [seq, json.dumps(tags or {}), mode, start, limit]).fetchone()["r"]
    return r


def names(route):
    return [s["name"] for s in route["spots"]]


def test_same_name_parent_and_too_close_are_not_chained(db):
    # 志賀島（海）と志賀島（港）：同じ名前・30m。香椎宮と境内の池：親子
    add(db, "志賀島", "coast", 0, 0)
    add(db, "志賀島", "port", 30, 0)
    shrine = add(db, "香椎宮", "shrine_temple", 2000, 0)
    add(db, "菖蒲池", "pond", 2100, 0, parent_id=shrine)
    add(db, "博多ふ頭", "port", 400, 0)
    add(db, "近すぎる倉庫", "warehouse", 40, 10)
    rs = routes(db, ["sea", "port_industrial"], mode="walk")
    assert rs, "流れが1件もない"
    for r in rs:
        assert names(r) != ["志賀島", "志賀島"]
        assert "近すぎる倉庫" not in names(r)          # 徒歩60m未満の区間は作らない
    assert names(rs[0]) == ["志賀島", "博多ふ頭"]
    assert not routes(db, ["historic", "lake"], mode="walk")   # 親子は並べない
    # 車は300m未満を同じ場所とみなす
    assert not [r for r in routes(db, ["sea", "port_industrial"], mode="car") if "博多ふ頭" not in names(r)]


def test_same_river_line_is_not_chained_and_overlaps_are_removed(db):
    add(db, "那珂川", "river", 0, 0, line_key="river:那珂川")
    add(db, "那珂川", "river", 300, 0, line_key="river:那珂川")
    add(db, "中洲の通り", "eatery_street", 150, 100)
    rs = routes(db, ["river", "commercial", "river"], mode="walk")
    assert rs == []                                     # 同じ川に戻る流れは作らない
    # 起点ごとに最良1件・重なり（場所を2つ以上共有）を除くので、同じ2地点の流れは1件だけ
    add(db, "大名の飲み屋街", "eatery_street", 700, 0)
    rs = routes(db, ["river", "commercial"], mode="walk")
    pairs = [tuple(sorted(names(r))) for r in rs]
    assert len(pairs) == len(set(pairs))


def test_score_parts_sum_to_match_and_untagged_search_can_score_high(db):
    add(db, "天神の繁華街", "eatery_street", 0, 0)
    add(db, "那珂川", "river", 200, 0)
    add(db, "（無名）川", "river", 900, 0)
    rs = routes(db, ["commercial", "river"], tags={"lonely": 1.0})   # データのないタグは点数に使わない
    assert rs
    for r in rs:
        parts, w = r["score_parts"], r["score_weights"]
        assert "tag" not in parts
        assert abs(sum(parts[k] * w[k] for k in parts) - float(r["match"])) < 0.005
        assert abs(sum(w.values()) - 1) < 0.005
    best = rs[0]
    assert names(best) == ["天神の繁華街", "那珂川"]
    assert float(best["match"]) >= 0.7


def test_human_tag_is_used_when_requested(db):
    a = add(db, "公園A", "park", 0, 0)
    add(db, "公園B", "park", 0, 300)
    add(db, "川", "river", 150, 150)
    db.execute("insert into spot_tags (spot_id, tag_code, origin, score) values (%s, 'nostalgic', 'human', 1)", [a])
    db.commit()
    rs = routes(db, ["green", "river"], tags={"nostalgic": 1.0})
    assert names(rs[0])[0] == "公園A"
    assert rs[0]["score_parts"]["signal"] == 1.0


def test_same_query_gives_same_result(db):
    for i in range(12):
        add(db, f"商店街{i}", "shotengai", i * 150, 0)
        add(db, f"川{i}", "river", i * 150, 200)
    a = routes(db, ["old_town", "river"])
    b = routes(db, ["old_town", "river"])
    assert [r["route_key"] for r in a] == [r["route_key"] for r in b]


def php_interpret(scene):
    code = ("require 'web/app/scene.php'; echo json_encode(interpret_by_keywords($argv[1]), JSON_UNESCAPED_UNICODE);")
    out = subprocess.run(["php", "-r", code, scene], cwd=ROOT, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_keywords_port_is_read_in_order():
    q = php_interpret("港の倉庫街を抜けて海沿いに出る")
    assert q["sequence"] == ["port_industrial", "sea"]
    q = php_interpret("夕方、車で港の倉庫街を抜けて海沿いに出る")
    assert q["sequence"] == ["port_industrial", "sea"] and q["travel_mode"] == "car"
    q = php_interpret("早朝の砂浜から、漁港の堤防へ歩く")
    assert q["sequence"] == ["beach", "sea"]
    q = php_interpret("神社の参道から坂道を上がり、高台から海を見下ろす")
    assert q["sequence"][:2] == ["historic", "slope"] and q["sequence"][-1] == "sea"


# ---------- 段階2：雰囲気の目安（DB変更210） ----------
def shop_categories(db):
    for code, name in (("bar", "バー・居酒屋"), ("restaurant", "飲食店"), ("cafe", "カフェ"), ("other_shop", "その他の店")):
        db.execute("insert into spot_categories (code, name, landscape_group) values (%s, %s, 'shop') on conflict do nothing", [code, name])
    db.commit()


def spatial(db, sid, **kw):
    v = {"open_ratio": 0.5, "building_cover_100": 0.3, "water_dist_m": None, "water_kind": None,
         "road_class": "residential", "road_width_m": 5.5, "confidence": "ok", **kw}
    db.execute("""insert into spot_spatial (spot_id, spatial_version, open_ratio, building_cover_100, water_dist_m, water_kind,
                  road_class, road_width_m, confidence) values (%s, 'test', %s, %s, %s, %s, %s, %s, %s)""",
               [sid, v["open_ratio"], v["building_cover_100"], v["water_dist_m"], v["water_kind"],
                v["road_class"], v["road_width_m"], v["confidence"]])
    db.commit()


def bars_around(db, dx, dy, n, cat="bar"):
    for i in range(n):
        add(db, f"店{dx}_{dy}_{i}", cat, dx + (i % 5) * 15 - 30, dy + (i // 5) * 15 - 30)


def downtown_and_mall(db):
    """中洲（飲み屋街・バー12軒）、郊外の商業施設（周りに店なし）、路地、川"""
    shop_categories(db)
    naka = add(db, "中洲の飲み屋街", "eatery_street", 0, 0)
    spatial(db, naka, road_width_m=6.0)
    bars_around(db, 0, 0, 12)
    mall = add(db, "長尾センタープラザ", "commercial_facility", 5000, 0)
    spatial(db, mall, building_cover_100=0.1, open_ratio=0.8)
    for sid, dx in ((naka, 0), (mall, 5000)):
        alley = add(db, f"路地{dx}", "alley", dx + 150, 120)
        spatial(db, alley, road_width_m=3.0)
    near = add(db, "那珂川（中洲）", "river", 300, 250)
    spatial(db, near, water_dist_m=5, water_kind="river")
    far = add(db, "郊外の川", "river", 5300, 250)
    spatial(db, far, water_dist_m=5, water_kind="river")
    db.execute("select vibe_apply()")
    db.commit()
    return naka, mall


def test_vibe_counts_and_tag_signal(db):
    naka, mall = downtown_and_mall(db)
    home = add(db, "住宅地", "housing_estate", 9000, 9000)
    spatial(db, home, road_width_m=3.5)
    db.execute("select vibe_apply()")
    db.commit()
    row = db.execute("select bars_150, shops_150 from spot_spatial where spot_id = %s", [naka]).fetchone()
    assert row["bars_150"] == 12 and row["shops_150"] == 12
    sig = lambda sid, tag: db.execute("select tag_signal(%s, %s) v", [sid, tag]).fetchone()["v"]
    assert sig(naka, "night_ok") >= 0.8
    assert sig(home, "lonely") >= 0.8
    assert sig(naka, "lonely") < 0.3
    assert sig(naka, "nostalgic") is None                      # データで測れないタグは null
    # 人のタグが優先
    db.execute("insert into spot_tags (spot_id, tag_code, origin, score) values (%s, 'nostalgic', 'human', 1)", [naka])
    db.execute("insert into spot_tags (spot_id, tag_code, origin, score) values (%s, 'lonely', 'ai', 1)", [mall])
    db.commit()
    assert sig(naka, "nostalgic") == 1.0
    # AIのタグは使わない（推定値のまま）
    assert db.execute("select tag_signal(%s, 'lonely') v", [mall]).fetchone()["v"] == \
        db.execute("select (signals ->> 'lonely')::real v from spot_spatial where spot_id = %s", [mall]).fetchone()["v"]


def test_group_typicality_commercial(db):
    naka, mall = downtown_and_mall(db)
    typ = lambda sid: db.execute("select group_typicality(%s, 'commercial') v", [sid]).fetchone()["v"]
    assert typ(mall) < typ(naka)
    assert typ(naka) >= 0.8 and typ(mall) <= 0.2


def test_night_scene_starts_downtown_and_river_cut_is_by_the_water(db):
    downtown_and_mall(db)
    tags = {"lonely": 1, "low_traffic": 1, "night_ok": 1, "narrow_street": 1, "river_view": 1}
    rs = routes(db, ["commercial", "old_town", "river"], tags=tags, mode="walk")
    assert rs
    assert names(rs[0])[0] == "中洲の飲み屋街"
    mall = [r for r in rs if names(r)[0] == "長尾センタープラザ"]
    assert mall and float(mall[0]["match"]) <= float(rs[0]["match"]) - 0.15   # 周りに店のない商業施設は大きく下がる
    for r in rs:
        if names(r)[0] != "中洲の飲み屋街":
            continue
        assert "signal" in r["score_parts"] and "typical" in r["score_parts"]
        river = r["spots"][-1]
        d = db.execute("select water_dist_m from spot_spatial where spot_id = %s", [river["id"]]).fetchone()["water_dist_m"]
        assert d < 50
        assert "バー・居酒屋12軒" in (r["spots"][0]["vibe"] or "")
        assert abs(sum(r["score_parts"][k] * r["score_weights"][k] for k in r["score_parts"]) - float(r["match"])) < 0.005


def test_vibe_stats_reads_without_writing(db):
    downtown_and_mall(db)
    st = db.execute("select vibe_stats() s").fetchone()["s"]
    assert st["spots"] >= 4 and len(st["percentiles_10_50_90_99"]["bars_150"]) == 4
