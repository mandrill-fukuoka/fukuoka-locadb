"""回帰テスト：登録地の自動チェック（locadb/verify.py。読み取りのみ）"""
import json

from locadb import verify
from locadb.verify import PlaceIndex, check_address, check_category, check_summary, check_summary_corpus, run_checks
from osmworld import World


def S(**kw):
    base = dict(id=1, code="FUK-CHU-S0001", name="地点", category="park", extra_categories=[], area_code="fuk-chuo",
                area_name="中央区", address="福岡市中央区天神一丁目", origin="osm", position_kind="point", line_key=None,
                catname="公園・庭園・緑地", shape="point", lat=33.5900, lon=130.4000, elevation_m=5.0, relief_m=0.0,
                terrain_status="ok", slope_kind=None, slope_grade_pct=None, slope_rise_m=None, slope_len_m=None,
                geo_area="fuk-chuo", geo_area_name="中央区", tags={})
    base.update(kw)
    return base


def checks(found):
    return sorted(f.check for f in found)


def test_category_rules():
    assert checks(check_category(S(name="警固神社", category="park"))) == ["name_category"]
    assert checks(check_category(S(name="警固神社", category="shrine_temple", catname="寺社"))) == []
    assert checks(check_category(S(name="九州大学病院", category="hospital"))) == []           # 「院」でも病院は寺社にしない
    assert checks(check_category(S(name="今泉公園", category="park"))) == []
    assert checks(check_category(S(name="福岡カトリック教会", category="shrine_temple", catname="寺社"))) == ["worship_not_temple"]
    assert checks(check_category(S(name="Shinto Shrine", category="shrine_temple", catname="寺社"))) == ["english_name"]
    assert checks(check_category(S(name="（無名）公園（天神一丁目）"))) == []
    assert checks(check_category(S(name="A;B橋", category="bridge_overpass"))) == ["multi_name"]
    # 線の地点（川の点など）には名前の規則を使わない
    assert checks(check_category(S(name="承天寺", category="alley", shape="line"))) == []
    # 坂道：車道で25%以上は誤りの疑い、階段は対象外
    assert checks(check_category(S(category="slope_street", slope_kind="road", slope_grade_pct=27.0, slope_len_m=60))) == ["slope_road_steep"]
    assert checks(check_category(S(category="slope_street", slope_kind="steps", slope_grade_pct=60.0))) == []
    assert checks(check_category(S(name="地点", category="beach", elevation_m=45.0))) == ["seaside_high"]


def test_facility_name_and_trunk_road_rules():
    # 施設名が景観・産業の種別に入っている
    assert checks(check_category(S(name="西鉄バス 営業所", category="port", catname="港湾"))) == ["name_facility"]
    assert checks(check_category(S(name="博多消防団千代分団車庫", category="factory", catname="工場"))) == ["name_facility"]
    assert checks(check_category(S(name="乗馬クラブ ルヴァード花畑", category="warehouse", catname="倉庫"))) == ["name_facility"]
    assert checks(check_category(S(name="さくらクリニック", category="hospital", catname="病院"))) == []
    assert checks(check_category(S(name="三菱倉庫 須崎事務所", category="warehouse", catname="倉庫"))) == []   # 事務所は対象外
    # 幹線道路が坂道・駅に入っている（駅名は除く）
    assert checks(check_category(S(name="福岡東環状線", category="slope_street", catname="坂道", shape="line"))) == ["trunk_road"]
    assert checks(check_category(S(name="県道54号", category="station", catname="駅前"))) == ["trunk_road"]
    assert checks(check_category(S(name="国体道路", category="station", catname="駅前"))) == ["trunk_road"]
    assert checks(check_category(S(name="香椎駅", category="station", catname="駅前"))) == []
    assert checks(check_category(S(name="平尾五丁目の坂道", category="slope_street", catname="坂道", shape="line"))) == []


def test_address_rules():
    assert checks(check_address(S())) == []
    assert checks(check_address(S(address="福岡市博多区中洲一丁目"))) == ["address_ward"]
    assert checks(check_address(S(address=None))) == ["address_missing"]
    assert checks(check_address(S(address=None, terrain_status="no_data"))) == []             # 海上は住所なしでよい
    assert checks(check_address(S(geo_area="fuk-hakata", geo_area_name="博多区"))) == ["area_outside"]
    assert checks(check_address(S(geo_area=None))) == []                                      # 境界の外（海など）は比べない
    assert checks(check_address(S(tags={"addr:city": "福岡市博多区"}))) == ["osm_addr_ward"]
    assert checks(check_address(S(tags={"addr:city": "福岡市中央区"}))) == []


def test_regeo_compares_town(monkeypatch):
    rows = [S(id=1), S(id=2, address="福岡市中央区大名二丁目"), S(id=3, address="福岡市中央区赤坂1丁目")]
    answers = {1: "福岡市中央区天神二丁目", 2: "福岡市中央区今泉一丁目", 3: "福岡市中央区赤坂一丁目"}
    it = iter(answers.values())
    found = verify.check_regeo(rows, lambda lon, lat, sess: next(it), workers=1)
    assert [(f.spot_id, f.check) for f in found] == [(2, "address_regeo")]                    # 丁目の違い・表記ゆれは指摘しない


def _places():
    return PlaceIndex([S(id=10, name="博多駅", lat=33.5897, lon=130.4207),
                       S(id=11, name="姪浜駅", lat=33.5833, lon=130.3260),
                       S(id=12, name="天神中央公園", lat=33.5920, lon=130.4030)])


def note(body, nid=100):
    return {"note_id": nid, "body": body, "source": "laya", "prompt_version": "laya-summary-v3"}


def test_summary_rules():
    s = S(name="今泉公園", lat=33.5880, lon=130.3990)
    p = _places()
    ok = "今泉公園は中央区の住宅地にある小さな公園。天神中央公園まで約450m。ベンチと植え込みがあり、周りは中低層の建物に囲まれている。"
    assert checks(check_summary(s, note(ok), p)) == []
    assert "sum_distance" in checks(check_summary(s, note(ok.replace("約450m", "約50m")), p))
    assert "sum_far_place" in checks(check_summary(s, note(ok + "姪浜駅の近くでもある。"), p))
    assert "sum_other_ward" in checks(check_summary(s, note(ok.replace("中央区", "博多区")), p))
    assert "sum_foreign" in checks(check_summary(s, note(ok + "这里很安静。"), p))
    assert "sum_unnamed" in checks(check_summary(s, note("（無名）公園（今泉一丁目）は" + ok), p))
    assert "sum_short" in checks(check_summary(s, note("小さな公園。"), p))
    assert "sum_forbidden" in checks(check_summary(s, note(ok + "治安がよく夜も安心。"), p))
    assert "sum_needs_source" in checks(check_summary(s, note(ok + "明治時代から有名な公園。"), p))
    # 城南区は「南区」と取り違えない
    j = S(name="地点", area_name="城南区")
    assert "sum_other_ward" not in checks(check_summary(j, note("城南区の住宅地にある。" + ok.replace("中央区", "城南区")), p))


def test_summary_numbers():
    s = S(name="浄水通りの坂", category="slope_street", slope_kind="road", slope_grade_pct=12.0, slope_rise_m=8.0,
          slope_len_m=70.0, elevation_m=20.0)
    p = _places()
    good = "浄水通りの坂は長さ約70m、平均勾配12%、高低差約8mの坂道。標高は約20mで、周りの住宅地を見下ろす。上りは南へ向かう。"
    assert checks(check_summary(s, note(good), p)) == []
    bad = good.replace("勾配12%", "勾配25%").replace("標高は約20m", "標高は約80m")
    assert checks(check_summary(s, note(bad), p)) == ["sum_number", "sum_number"]


def test_summary_corpus():
    rows = [S(id=i, name=f"公園{i}号", lat=33.59 + i * 0.001) for i in range(30)]
    pairs = [(s, note(f"{s['name']}は中央区にある緑の多い公園です。ベンチが{i}基ある。", 100 + i)) for i, s in enumerate(rows)]
    pairs.append((S(id=99, name="X"), note("同じ文章。" * 20, 999)))
    pairs.append((S(id=98, name="Y"), note("同じ文章。" * 20, 998)))
    found = check_summary_corpus(pairs, min_template=25)
    c = checks(found)
    assert c.count("sum_template") == 30 and c.count("sum_duplicate") == 2


def test_dry_run_reads_db_and_writes_nothing(db, tmp_path, monkeypatch):
    """本物のDB（架空の地図）で全チェックを流す。DBは変わらない"""
    w = World()
    w.point(10, 130.385, 33.585, amenity="place_of_worship", name="寺A")
    w.point(11, 130.3851, 33.5851, leisure="park", name="寺A")                                # 同じ名前が近い
    w.area(20, 130.390, 33.586, leisure="park", name="春吉神社")                               # 名前と種別が合わない
    w.run(db, tmp_path)
    a = db.execute("select id from spots where name='寺A' and category='shrine_temple'").fetchone()["id"]
    db.execute("select ai_submit('laya', %s::jsonb, 'm', 'laya-summary-v3')",
               (json.dumps([{"spot_id": a, "summary": "寺Aは東区の寺。这里很安静。"}], ensure_ascii=False),))
    db.commit()
    before = db.execute("select count(*) n, max(updated_at) u from spots").fetchone()
    verify.main(["--dry-run", "--out", str(tmp_path / "out")])
    after = db.execute("select count(*) n, max(updated_at) u from spots").fetchone()
    assert before == after
    rows = json.loads((tmp_path / "out" / "findings.json").read_text(encoding="utf-8"))
    got = {r["check"] for r in rows}
    assert {"same_name_near", "name_category", "sum_foreign", "sum_short"} <= got
    assert (tmp_path / "out" / "findings.csv").exists() and "自動チェック" in (tmp_path / "out" / "summary.md").read_text(encoding="utf-8")
    # 本番と同じく run_checks は読み取ったデータだけで動く
    assert run_checks([], [], []) == []


def test_summary_false_positive_guards():
    p = PlaceIndex([S(id=10, name="荒木田稲荷", lat=33.5880, lon=130.3992),
                    S(id=11, name="大濠公園", lat=33.5860, lon=130.3770, extent_m=900),
                    S(id=12, name="ヤクルト事業創業の地", lat=33.5881, lon=130.3991)])
    s = S(name="秋葉社", lat=33.5880, lon=130.3990)
    base = "秋葉社は中央区の住宅地にある小さな社。境内は狭く、周りは低い家並みが続く。"
    # 「半径400m以内に〜」の400は上限。近くにあれば正しい
    assert "sum_distance" not in checks(check_summary(s, note(base + "荒木田稲荷が400m以内にある。"), p))
    # 読点をはさんだ数値は別の話
    assert "sum_distance" not in checks(check_summary(s, note(base + "荒木田稲荷、標高12mの高台。"), p))
    # 広い公園は代表点からの距離から広がりの半分を引く（端までは近い）
    t = S(name="店", lat=33.5860, lon=130.3830)
    assert "sum_distance" not in checks(check_summary(t, note(base + "大濠公園から約150m。"), p))
    # 地点名の中の「創業」は出典の指摘にしない
    assert "sum_needs_source" not in checks(check_summary(s, note(base + "ヤクルト事業創業の地がすぐ隣にある。"), p))
    assert "sum_garbled" in checks(check_summary(s, note(base + "福�aga市にある。"), p))
    # 境界の橋：本文が住所の区に合わせていれば「別の区」にしない
    b = S(name="箕島橋", category="bridge_overpass", address="福岡市博多区美野島一丁目")
    assert "sum_other_ward" not in checks(check_summary(b, note("箕島橋は博多区と中央区を結ぶ橋。" + base), p))
    assert checks(check_address(b)) == ["address_ward_edge"]
    assert checks(check_category(S(name="福岡保養院", category="hospital"))) == []
    assert checks(check_category(S(name="マリゾン桟橋", category="port"))) == []


def test_line_places_and_generic_names_are_not_measured():
    p = PlaceIndex([S(id=10, name="九州新幹線", category="railway", shape="line", lat=33.62, lon=130.45),
                    S(id=11, name="駐車場", lat=33.70, lon=130.50)])
    s = S(name="営業所", lat=33.5600, lon=130.4300)
    base = "営業所は南区の幹線道路沿いにある。周りは倉庫と住宅が混じる。"
    found = checks(check_summary(s, note(base + "九州新幹線が251m先を通る。駐車場が隣にある。"), p))
    assert "sum_distance" not in found and "sum_far_place" not in found


def _world_with_findings(db, tmp_path):
    w = World()
    w.point(10, 130.385, 33.585, amenity="place_of_worship", name="寺A")
    w.area(20, 130.390, 33.586, leisure="park", name="春吉神社")                               # 名前と種別が合わない
    w.run(db, tmp_path)
    a = db.execute("select id from spots where name='寺A'").fetchone()["id"]
    db.execute("select ai_submit('laya', %s::jsonb, 'm', 'laya-summary-v4')",
               (json.dumps([{"spot_id": a, "summary": "寺Aは中央区の寺。治安がよい。这里很安静。"}], ensure_ascii=False),))
    db.commit()
    return a


def open_checks(db):
    return sorted((r["check_code"], r["status"]) for r in db.execute("select * from spot_checks"))


def test_apply_stores_resolves_and_keeps_dismissed(db, tmp_path):
    a = _world_with_findings(db, tmp_path)
    verify.main(["--apply", "--out", str(tmp_path / "o1")])
    st = {r["check_code"]: r for r in db.execute("select * from spot_checks")}
    assert {"name_category", "sum_foreign", "sum_forbidden", "sum_short"} <= set(st)
    assert all(r["status"] == "open" for r in st.values())
    assert st["sum_foreign"]["label"] == "中国語・ハングルの混入" and st["sum_foreign"]["note_id"] is not None
    job = db.execute("select * from job_runs where job='verify'").fetchone()
    n_rows = db.execute("select count(*) n from spot_checks").fetchone()["n"]
    assert job["finished_at"] is not None and job["stats"]["saved"] == n_rows

    # 画面：件数・一覧・「問題なし」で閉じる
    counts = db.execute("select checks_counts() r").fetchone()["r"]
    assert any(i["check_code"] == "sum_foreign" and i["n"] == 1 for i in counts["items"])
    lst = db.execute("select checks_list('summary') r").fetchone()["r"]
    assert lst[0]["severity"] == "warn" and "这" in lst[0]["note_body"]
    nc = st["name_category"]["id"]
    db.execute("select check_close(%s, 'hirata', '神社の境内の公園')", (nc,)); db.commit()
    assert db.execute("select event from spot_history where event='check_dismissed'").fetchone()
    # 下書きのタブ：指摘が付き、指摘のあるものが先
    d = db.execute("select ai_drafts() r").fetchone()["r"]
    assert d[0]["spot_id"] == a and {c["label"] for c in d[0]["checks"]} >= {"中国語・ハングルの混入"}
    assert db.execute("select ai_drafts(p_flagged => false) r").fetchone()["r"] == []

    # 2回目：直した下書きの指摘は resolved、閉じた指摘は開き直さない
    db.execute("select ai_submit('laya', %s::jsonb, 'm', 'laya-summary-v5')",
               (json.dumps([{"spot_id": a, "summary": "寺Aは中央区の住宅地にある寺。石畳の参道と古い山門があり、周りは低い家並みが続く。"}],
                           ensure_ascii=False),))
    db.commit()
    verify.main(["--apply", "--out", str(tmp_path / "o2")])
    rows = {(r["check_code"], r["note_id"] is not None): r["status"] for r in db.execute("select * from spot_checks")}
    assert rows[("sum_foreign", True)] == "resolved"
    assert rows[("name_category", False)] == "dismissed"
    assert not any(c == "sum_foreign" for c, s in open_checks(db) if s == "open")


def test_apply_reopens_resolved_when_seen_again(db, tmp_path):
    _world_with_findings(db, tmp_path)
    verify.main(["--apply", "--out", str(tmp_path / "o1")])
    db.execute("update spot_checks set status='resolved' where check_code='name_category'"); db.commit()
    verify.main(["--apply", "--out", str(tmp_path / "o2")])
    assert db.execute("select status from spot_checks where check_code='name_category'").fetchone()["status"] == "open"
