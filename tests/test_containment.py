"""回帰テスト：境内・敷地内の地点の親子（DB変更208・locadb/containment.py）"""
import json

from locadb.containment import CONTAINER_MIN_M2, container_of
from osmworld import World, spot
from shapely.geometry import box


def rpc(db, sql, *args):
    return db.execute(f"select {sql} r", args).fetchone()["r"]


def make_world():
    """博多区に、神社の境内（約185m四方）と、その中の社・碑・池。外に別の神社。
    中央区に大きな公園（約370m四方）と、その中の神社とその境内の末社"""
    w = World()
    w.area(3001, 130.44, 33.59, size=0.0009, amenity="place_of_worship", religion="shinto", name="香椎宮")
    w.point(4001, 130.4402, 33.5902, historic="yes", name="本殿")
    w.point(4002, 130.4395, 33.5897, amenity="place_of_worship", name="弁財天社")
    w.area(3002, 130.4404, 33.5896, size=0.0001, natural="water", water="pond", name="菖蒲池")
    w.point(4003, 130.4450, 33.5950, amenity="place_of_worship", name="外の神社")
    w.area(3003, 130.40, 33.59, size=0.0018, leisure="park", name="大きな公園")
    w.area(3004, 130.4005, 33.5905, size=0.0003, amenity="place_of_worship", name="公園の神社")
    w.point(4004, 130.4006, 33.5906, amenity="place_of_worship", name="末社")
    w.point(4005, 130.3990, 33.5890, leisure="playground", historic="memorial", name="記念碑")
    w.area(3005, 130.39, 33.585, size=0.0002, leisure="park", name="小さな公園")      # 2ha 未満は容れ物にしない
    w.point(4006, 130.3900, 33.5851, historic="memorial", name="小公園の碑")
    return w


def test_container_rules():
    big = box(0, 0, 200, 200)
    assert container_of("w1", ["shrine_temple"], {}, box(0, 0, 10, 10)).cat == "shrine_temple"   # 寺社は大きさを問わない
    assert container_of("w1", ["park"], {}, big).cat == "park"
    assert container_of("w1", ["park"], {}, box(0, 0, 100, 100)) is None                      # 1ha：小さな公園は対象外
    assert CONTAINER_MIN_M2["park"] == 20_000
    assert container_of("w1", ["school"], {"amenity": "university"}, big).cat == "school"
    assert container_of("w1", ["school"], {"amenity": "school"}, big) is None                  # 小中高は対象外


def test_parents_and_groups(db, tmp_path):
    make_world().run(db, tmp_path)
    s = {n: spot(db, name=n) for n in ["香椎宮", "本殿", "弁財天社", "菖蒲池", "外の神社",
                                       "大きな公園", "公園の神社", "末社", "記念碑", "小さな公園", "小公園の碑"]}
    root = s["香椎宮"]["id"]
    for n in ["本殿", "弁財天社"]:                          # 同じ景観（歴史・寺社）→ 一覧でまとめる
        assert (s[n]["parent_id"], s[n]["group_root_id"], s[n]["parent_source"]) == (root, root, "osm")
    assert (s["菖蒲池"]["parent_id"], s["菖蒲池"]["group_root_id"]) == (root, None)   # 池は親だけ示し、一覧は別
    assert s["外の神社"]["parent_id"] is None and s["香椎宮"]["parent_id"] is None
    # 公園の中の神社は公園の子だが景観が違うのでまとめない。末社は直接の親（神社）にまとめる
    assert (s["公園の神社"]["parent_id"], s["公園の神社"]["group_root_id"]) == (s["大きな公園"]["id"], None)
    assert (s["末社"]["parent_id"], s["末社"]["group_root_id"]) == (s["公園の神社"]["id"], s["公園の神社"]["id"])
    assert s["記念碑"]["parent_id"] == s["大きな公園"]["id"]
    assert s["小公園の碑"]["parent_id"] is None             # 2ha 未満の公園は容れ物にしない


def test_search_detail_nearby(db, tmp_path):
    make_world().run(db, tmp_path)
    root = spot(db, name="香椎宮")["id"]
    hits = rpc(db, "search_spots(p_area => 'fuk-hakata')")
    names = [h["name"] for h in hits]
    assert "香椎宮" in names and "本殿" not in names and "弁財天社" not in names    # 境内の社はまとめる
    assert "菖蒲池" in names                                                      # 池は別に出す
    kashii = next(h for h in hits if h["name"] == "香椎宮")
    assert kashii["others"] == 2 and kashii["precinct_group"] is True
    pond = next(h for h in hits if h["name"] == "菖蒲池")
    assert pond["parent"] == {"id": root, "name": "香椎宮", "word": "境内"}
    # 語で探すと子が単独で出て、親が分かる
    honden = rpc(db, "search_spots(p_q => '本殿')")
    assert [h["name"] for h in honden] == ["本殿"] and honden[0]["parent"]["name"] == "香椎宮" and honden[0]["others"] == 0
    # 地点ページ：親の境内の地点と、子の親
    d = rpc(db, "spot_detail(%s)", root)
    assert d["children_word"] == "境内"
    assert sorted(c["name"] for c in d["children"]) == ["弁財天社", "本殿", "菖蒲池"]
    assert rpc(db, "spot_detail(%s)", spot(db, name="本殿")["id"])["parent"]["name"] == "香椎宮"
    # 周辺の景観：自分の境内は出さない。ほかの地点の周辺では、まとめた子は出さず親だけ
    near_root = [n["name"] for n in rpc(db, "spots_nearby(%s)", root)]
    assert not {"本殿", "弁財天社", "菖蒲池"} & set(near_root)
    near_out = [n["name"] for n in rpc(db, "spots_nearby(%s, 1500)", spot(db, name="外の神社")["id"])]
    assert "香椎宮" in near_out and "本殿" not in near_out and "菖蒲池" in near_out


def test_manual_and_archive(db, tmp_path):
    w = make_world()
    w.run(db, tmp_path)
    honden = spot(db, name="本殿")["id"]
    db.execute("update spots set parent_id=null, group_root_id=null, parent_source='none' where id=%s", (honden,))
    db.commit()
    w.run(db, tmp_path)                                     # 人が「まとめない」とした地点は自動で付け直さない
    assert spot(db, name="本殿")["parent_source"] == "none" and spot(db, name="本殿")["parent_id"] is None
    # 親が対象外になったら、一覧ではまとめない（子は単独で出る）
    root = spot(db, name="香椎宮")["id"]
    db.execute("update spots set data_status='archived' where id=%s", (root,))
    db.commit()
    names = [h["name"] for h in rpc(db, "search_spots(p_area => 'fuk-hakata')")]
    assert "弁財天社" in names


def test_dry_run_rolls_back(db, tmp_path):
    res = make_world().run(db, tmp_path, mode="dry_run")
    assert res["summary"]["contained"]["children"] >= 5
    assert "境内・敷地内の地点" in res["report"]
    assert db.execute("select count(*) n from spots").fetchone()["n"] == 0
