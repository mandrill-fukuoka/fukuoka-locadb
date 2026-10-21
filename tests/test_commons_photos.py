"""回帰テスト：写真候補（Wikimedia Commons。DB変更200）。外部へは出ない（応答は手で作る）"""
import pytest

from locadb import commons_photos as cp
from osmworld import World, spots


def page(title, lic="CC BY-SA 4.0", author="<a href='x'>山田 太郎</a>", lat=33.5851, lon=130.3901, nonfree=None, thumb=True,
         desc="警固神社の<b>本殿</b>"):
    return {"title": title, "coordinates": [{"lat": lat, "lon": lon}],
            "imageinfo": [{"thumburl": "https://upload.wikimedia.org/x/640px-a.jpg" if thumb else None,
                           "descriptionurl": "https://commons.wikimedia.org/wiki/" + title.replace(" ", "_"),
                           "extmetadata": {"LicenseShortName": {"value": lic}, "Artist": {"value": author},
                                           "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
                                           "ImageDescription": {"value": desc},
                                           "NonFree": {"value": nonfree}}}]}


SPOT = {"id": 1, "name": "警固神社", "lat": 33.585, "lon": 130.39}


def test_license_rules():
    ok = ["CC BY-SA 4.0", "CC BY 3.0", "CC0", "Public domain", "CC BY-SA 2.5 JP"]
    ng = ["CC BY-NC 4.0", "CC BY-ND 2.0", "Fair use", "", "All rights reserved", "CC BY-NC-SA 3.0"]
    assert all(cp.license_ok(x) for x in ok) and not any(cp.license_ok(x) for x in ng)
    assert not cp.license_ok("CC BY 4.0", "true")


def test_candidates_filter_rank_and_clean():
    data = {"query": {"pages": [
        page("File:Kego Shrine 警固神社.jpg", lat=33.5851),                       # 名前一致・近い
        page("File:Street nearby.jpg", lat=33.5853, desc="a street"),                              # 名前なし・少し遠い
        page("File:NC photo.jpg", lic="CC BY-NC 4.0"),                           # NC → 除く
        page("File:No author.jpg", author=""),                                    # 作者なし → 除く
        page("File:No thumb.jpg", thumb=False),                                   # 画像なし → 除く
    ]}}
    got = cp.candidates_of(SPOT, data)
    assert [c["file_title"] for c in got] == ["File:Kego Shrine 警固神社.jpg", "File:Street nearby.jpg"]
    assert got[0]["name_match"] and not got[1]["name_match"]
    assert got[0]["author"] == "山田 太郎" and "<" not in got[0]["description"]       # HTMLは除く
    assert got[0]["dist_m"] < got[1]["dist_m"]


def test_collect_apply_decide_and_resume(db, tmp_path):
    w = World()
    w.point(10, 130.39, 33.585, amenity="place_of_worship", name="警固神社")
    w.point(11, 130.40, 33.590, amenity="place_of_worship", name="寺B")
    w.run(db, tmp_path)
    by = {s["name"]: s for s in spots(db)}

    class Sess:
        def get(self, url, params=None, timeout=None, headers=None):
            class R:
                status_code = 200
                def json(_):
                    lat = float(params["ggscoord"].split("|")[0])
                    return {"query": {"pages": [page("File:Kego Shrine 警固神社.jpg")]}} if abs(lat - 33.585) < 1e-3 else {}
            return R()

    found, st, marks = cp.collect(db, 10, Sess(), sleep=lambda s: None)
    assert st["spots"] == 2 and st["with_candidates"] == 1 and st["candidates"] == 1
    with db.cursor() as cur:
        cur.executemany(cp.INSERT_SQL, found); cur.executemany(cp.MARK_SQL, marks)
    db.commit()
    # 探した地点は次回に含めない（候補なしの地点も）
    assert cp.collect(db, 10, Sess(), sleep=lambda s: None)[1]["spots"] == 0
    # 採否：採用すると出典つきで spot_photos に入る。公開の地点詳細にも出る
    cand = db.execute("select photo_candidates_list() r").fetchone()["r"][0]
    assert cand["name"] == "警固神社" and cand["status"] == "candidate"
    assert db.execute("select photos_counts() r").fetchone()["r"]["candidate"] == 1
    assert not db.execute("select photo_decide(%s,'adopt','') r", (cand["id"],)).fetchone()["r"]["ok"]      # 人の名前が必要
    assert db.execute("select photo_decide(%s,'adopt','hirata') r", (cand["id"],)).fetchone()["r"]["ok"]
    ph = db.execute("select spot_detail(%s) r", (by["警固神社"]["id"],)).fetchone()["r"]["photos"]
    assert len(ph) == 1 and ph[0]["author"] == "山田 太郎" and ph[0]["license"] == "CC BY-SA 4.0" and ph[0]["source_url"].startswith("https://")
    row = db.execute("select search_spots(p_q => '警固神社') r").fetchone()["r"][0]       # 一覧にも出典つきで出る
    assert row["photo"]["author"] == "山田 太郎" and row["photo"]["license"] == "CC BY-SA 4.0"
    # 候補に戻すと写真も外れる／不採用
    db.execute("select photo_decide(%s,'reset','hirata')", (cand["id"],))
    assert db.execute("select spot_detail(%s) r", (by["警固神社"]["id"],)).fetchone()["r"]["photos"] == []
    db.execute("select photo_decide(%s,'reject','hirata')", (cand["id"],))
    assert db.execute("select photos_counts() r").fetchone()["r"] == {"candidate": 0, "adopted": 0, "rejected": 1, "spots": 0}
