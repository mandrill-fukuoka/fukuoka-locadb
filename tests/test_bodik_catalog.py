"""回帰テスト：BODIK の棚卸し（locadb/bodik_catalog.py）。外部へは出ない"""
import json

from locadb import bodik_catalog as bc


class Resp:
    def __init__(self, body):
        self.status_code, self.content = 200, json.dumps(body).encode("utf-8")

    def json(self):
        return json.loads(self.content.decode("utf-8"))


class Session:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append(params["start"])
        return Resp({"result": {"count": 3, "results": self.pages[params["start"] // 2]}})


def test_all_packages_pages_until_count(monkeypatch):
    monkeypatch.setattr(bc, "ROWS", 2)
    s = Session([[{"name": "a"}, {"name": "b"}], [{"name": "c"}]])
    got = bc.all_packages(s, lambda _: None)
    assert [p["name"] for p in got] == ["a", "b", "c"] and s.calls == [0, 2]


def test_summarize_marks_used_and_formats():
    r = bc.summarize({"name": "401307_aed", "title": "福岡市　AED", "license_id": "cc-by", "metadata_modified": "2026-03-23T23:35:54",
                      "resources": [{"format": "CSV"}, {"format": ".xlsx"}], "tags": [{"name": "位置情報"}]})
    assert r["used"] and r["formats"] == "CSV/XLSX" and r["title"] == "福岡市 AED" and r["modified"] == "2026-03-23"
    assert bc.summarize({"name": "x", "resources": []})["used"] == ""
