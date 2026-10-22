"""回帰テスト：Mapillary 写真候補（DB変更201）。外部へは出ない（応答は手で作る）"""
from locadb import mapillary_photos as mp

SPOT = {"id": 1, "name": "テスト橋", "lat": 33.59, "lon": 130.40}


def im(i, dlat, ang, user="alice", pano=False, url="https://x/t.jpg", ts=1700000000000):
    return {"id": str(i), "captured_at": ts, "compass_angle": ang, "is_pano": pano, "thumb_1024_url": url,
            "creator": {"username": user} if user else {}, "geometry": {"coordinates": [130.40, 33.59 + dlat]}}


def test_filters_and_ranking():
    data = {"data": [im(1, 0.0002, 10), im(2, 0.0001, 15), im(3, 0.0003, 120), im(4, 0.00005, 200, user=""),
                     im(5, 0.00005, 250, pano=True), im(6, 0.00005, 300, url=None)]}
    cs = mp.candidates_of(SPOT, data)
    assert [c["file_title"] for c in cs] == ["mapillary:2", "mapillary:3"]   # 近い順・向きが近いもの(1)と作者なし等は除く
    c = cs[0]
    assert c["license"] == "CC BY-SA 4.0" and c["author"] == "alice"
    assert c["page_url"].startswith("https://www.mapillary.com/") and c["shot_at"] == "2023-11-14"


def test_bbox_contains_point():
    a, b, c, d = map(float, mp.bbox(33.59, 130.40).split(","))
    assert a < 130.40 < c and b < 33.59 < d


def test_missing_token(monkeypatch):
    import pytest
    monkeypatch.delenv("MAPILLARY_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        mp.main(["--dry-run"])
