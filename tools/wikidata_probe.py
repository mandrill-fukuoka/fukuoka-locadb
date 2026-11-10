"""Wikidata が既存の地点にどれだけ結べ、どんな事実が取れるかを数える（読み取りだけ。DBへは SELECT のみ。DBも本番も変えない）

  python -m tools.wikidata_probe --out DIR

  1) OSM の wikidata タグで結べる地点（features.tags）を数える
  2) 結べた Q 番号の項目から、取れる事実（開設年・管理者・公式サイト・画像・指定など）の埋まり具合を数える
  3) 結べた項目が「撮影場所」になっている作品（P915）の件数を数える
  4) タグのない地点に、福岡市周辺の座標つき Wikidata 項目を名前×距離で照合し、結べそうな候補を数える
決まり：Wikidata（CC0）への取得は直列・1.1秒間隔。キーや個人情報は読まない。取り込みはしない（数えるだけ）。
"""
import argparse
import csv
import collections
import json
import math
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

from locadb.db import connect

UA = "FukuokaLocaDB/1.0 (https://github.com/mandrill-fukuoka/fukuoka-locadb; wikidata probe, read-only)"
API = "https://www.wikidata.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"
BBOX = (33.40, 130.10, 33.75, 130.55)   # 南, 西, 北, 東（福岡市7区を含む外枠。照合で距離を見る）

PROPS = {  # 事実としてカードに載せうる項目
    "P571": "開設・創設（設立）", "P1619": "開館・開業日", "P137": "運営者", "P127": "所有者", "P856": "公式サイト",
    "P18": "画像", "P1435": "文化財などの指定", "P84": "建築家", "P149": "建築様式", "P1082": "人口",
    "P2046": "面積", "P2048": "高さ", "P131": "所在する行政区分", "P31": "分類（instance of）",
    "P1448": "正式名称", "P1449": "別名の通称", "P373": "Commonsカテゴリ", "P276": "所在地（場所）", "P912": "施設の用途",
}

_last = [0.0]


def _get(url, data=None):
    wait = 1.1 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    for attempt in range(4):
        _last[0] = time.time()
        req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except Exception as e:  # 429/5xx などは待って再試行
            print("retry", attempt, type(e).__name__, str(e)[:80], flush=True)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("取得に失敗: " + url[:120])


def norm(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"[\s・･\-ー_/（）()「」『』【】.,、。]+", "", s)


def hav(a_lat, a_lon, b_lat, b_lon):
    p = math.pi / 180
    x = math.sin((b_lat - a_lat) * p / 2) ** 2 + math.cos(a_lat * p) * math.cos(b_lat * p) * math.sin((b_lon - a_lon) * p / 2) ** 2
    return 12742000 * math.asin(math.sqrt(x))


def load_spots():
    with connect() as conn:
        spots = conn.execute(
            "select s.id, s.name, s.category, s.origin, s.data_status, st_y(s.geom::geometry) lat, st_x(s.geom::geometry) lon "
            "from spots s where s.data_status <> 'archived'").fetchall()
        feats = conn.execute(
            "select sf.spot_id, f.key, f.tags->>'wikidata' wd, f.tags->>'wikipedia' wp, f.tags->>'name:en' en "
            "from spot_features sf join features f on f.key = sf.feature_key").fetchall()
    return spots, feats


def fetch_entities(qids):
    """SPARQL で各Qの存在・日本語ラベル・日本語版Wikipedia記事・各プロパティの有無を取る。{Q: {exists, label, jawiki, props}}"""
    out = {q: {"exists": False, "label": "", "jawiki": False, "props": set()} for q in qids}
    qids = sorted(qids)
    pv = " ".join("wdt:" + p for p in PROPS)
    for i in range(0, len(qids), 100):
        vals = " ".join("wd:" + q for q in qids[i:i + 100])
        q1 = ("SELECT ?q ?m ?l ?ja WHERE { VALUES ?q { %s } OPTIONAL { ?q schema:dateModified ?m } "
              "OPTIONAL { ?q rdfs:label ?l FILTER(LANG(?l) = \"ja\") } "
              "OPTIONAL { ?ja schema:about ?q ; schema:isPartOf <https://ja.wikipedia.org/> } }" % vals)
        for b in _get(SPARQL, urllib.parse.urlencode({"query": q1, "format": "json"}).encode())["results"]["bindings"]:
            e = out[b["q"]["value"].rsplit("/", 1)[1]]
            e["exists"] = e["exists"] or "m" in b
            e["label"] = e["label"] or b.get("l", {}).get("value", "")
            e["jawiki"] = e["jawiki"] or "ja" in b
        q2 = "SELECT DISTINCT ?q ?p WHERE { VALUES ?q { %s } VALUES ?p { %s } ?q ?p ?v }" % (vals, pv)
        for b in _get(SPARQL, urllib.parse.urlencode({"query": q2, "format": "json"}).encode())["results"]["bindings"]:
            out[b["q"]["value"].rsplit("/", 1)[1]]["props"].add(b["p"]["value"].rsplit("/", 1)[1])
        print("entities", min(i + 100, len(qids)), "/", len(qids), flush=True)
    return out


def fetch_filming(qids):
    """各Qを P915（撮影場所）にもつ作品。{Q: [(作品Q, ラベル)]}"""
    res = collections.defaultdict(list)
    qids = sorted(qids)
    for i in range(0, len(qids), 100):
        vals = " ".join("wd:" + q for q in qids[i:i + 100])
        q = ("SELECT ?loc ?work ?workLabel WHERE { VALUES ?loc { %s } ?work wdt:P915 ?loc . "
             "SERVICE wikibase:label { bd:serviceParam wikibase:language \"ja,en\". } }" % vals)
        d = _get(SPARQL, urllib.parse.urlencode({"query": q, "format": "json"}).encode())
        for b in d["results"]["bindings"]:
            res[b["loc"]["value"].rsplit("/", 1)[1]].append((b["work"]["value"].rsplit("/", 1)[1], b.get("workLabel", {}).get("value", "")))
    return res


def fetch_bbox_items():
    s, w, n, e = BBOX
    q = ("SELECT ?item ?itemLabel ?lat ?lon (GROUP_CONCAT(DISTINCT ?instLabel; separator=\"/\") AS ?inst) WHERE { "
         "SERVICE wikibase:box { ?item wdt:P625 ?c . bd:serviceParam wikibase:cornerSouthWest \"Point(%s %s)\"^^geo:wktLiteral . "
         "bd:serviceParam wikibase:cornerNorthEast \"Point(%s %s)\"^^geo:wktLiteral . } "
         "OPTIONAL { ?item wdt:P31 ?i . ?i rdfs:label ?instLabel . FILTER(LANG(?instLabel) = \"ja\") } "
         "BIND(geof:latitude(?c) AS ?lat) BIND(geof:longitude(?c) AS ?lon) "
         "SERVICE wikibase:label { bd:serviceParam wikibase:language \"ja,en\". } } GROUP BY ?item ?itemLabel ?lat ?lon" % (w, s, e, n))
    d = _get(SPARQL, urllib.parse.urlencode({"query": q, "format": "json"}).encode())
    rows = []
    for b in d["results"]["bindings"]:
        rows.append({"q": b["item"]["value"].rsplit("/", 1)[1], "label": b.get("itemLabel", {}).get("value", ""),
                     "lat": float(b["lat"]["value"]), "lon": float(b["lon"]["value"]), "inst": b.get("inst", {}).get("value", "")})
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="wikidata-out"); a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    spots, feats = load_spots()
    by_id = {s["id"]: s for s in spots}
    print("spots", len(spots), "features", len(feats), flush=True)

    # 1) タグで結べる地点
    linked = collections.defaultdict(set)      # spot_id -> {Q}
    with_wp = set()
    for f in feats:
        if f["spot_id"] not in by_id:
            continue
        if f["wd"] and re.fullmatch(r"Q\d+", f["wd"].strip()):
            linked[f["spot_id"]].add(f["wd"].strip())
        if f["wp"]:
            with_wp.add(f["spot_id"])
    qids = {q for v in linked.values() for q in v}
    fkeys = collections.defaultdict(list)       # spot_id -> [OSM要素 n123/w123/r123]
    for f in feats:
        if f["key"] and re.fullmatch(r"[nwr]\d+", f["key"]):
            fkeys[f["spot_id"]].append(f["key"])

    # 2) 事実の埋まり具合
    ents = fetch_entities(qids) if qids else {}
    cov = collections.Counter(); jawiki = 0; missing = 0
    for q in qids:
        e = ents[q]
        if not e["exists"]:
            missing += 1; continue
        for p in e["props"]:
            cov[p] += 1
        jawiki += e["jawiki"]
    # 3) 撮影場所
    film = fetch_filming(qids) if qids else {}

    # 4) タグのない地点との照合
    items = fetch_bbox_items()
    print("bbox items", len(items), flush=True)
    items = [i for i in items if not re.fullmatch(r"Q\d+", i["label"])]   # ラベルなし（Q番号のまま）は除く
    unlinked = [s for s in spots if s["id"] not in linked]
    taken = qids
    cands = []
    for it in items:
        if it["q"] in taken:
            continue
        ni = norm(it["label"])
        best = None
        for s in unlinked:
            if abs(s["lat"] - it["lat"]) > 0.005 or abs(s["lon"] - it["lon"]) > 0.006:   # 約500m の枠
                continue
            d = hav(s["lat"], s["lon"], it["lat"], it["lon"])
            ns = norm(s["name"])
            if ni and ns and ni == ns and d <= 500:
                kind = "名前一致"
            elif ni and ns and len(min(ni, ns, key=len)) >= 3 and (ni in ns or ns in ni) and d <= 300:
                kind = "名前の一部一致"
            elif d <= 30:
                kind = "近接のみ"
            else:
                continue
            rank = {"名前一致": 0, "名前の一部一致": 1, "近接のみ": 2}[kind]
            if best is None or (rank, d) < (best[0], best[1]):
                best = (rank, d, kind, s)
        if best:
            cands.append({"q": it["q"], "label": it["label"], "inst": it["inst"], "kind": best[2], "dist_m": round(best[1]),
                          "spot_id": best[3]["id"], "spot_name": best[3]["name"], "category": best[3]["category"],
                          "osm": " ".join(fkeys.get(best[3]["id"], []))})
    kinds = collections.Counter(c["kind"] for c in cands)
    cand_spots = {c["spot_id"] for c in cands if c["kind"] != "近接のみ"}

    # 出力
    n = len(spots)
    lines = [f"## Wikidata 結び付きの試算（読み取りのみ）", "",
             f"- 対象の地点（archived を除く）：{n}件", f"- OSM の wikidata タグで結べた地点：{len(linked)}件（{len(linked) / n:.1%}）、Q番号は {len(qids)} 種",
             f"- うち Wikidata に項目が存在しない（取得できない）Q：{missing}",
             f"- OSM の wikipedia タグがある地点：{len(with_wp)}件、そのうち wikidata でも結べた地点：{len(with_wp & set(linked))}件",
             f"- 結べた項目で日本語版Wikipediaの記事があるもの：{jawiki}", "",
             "### 結べた項目で取れる事実（項目数／結べたQ数）", "", "| プロパティ | 内容 | 件数 | 割合 |", "|---|---|---|---|"]
    tot = max(len(qids) - missing, 1)
    for p, label in PROPS.items():
        lines.append(f"| {p} | {label} | {cov[p]} | {cov[p] / tot:.0%} |")
    lines += ["", f"### 撮影場所（P915）として作品に載っている項目", "",
              f"- 作品をもつ項目：{len(film)}件、作品の延べ数：{sum(len(v) for v in film.values())}", "",
              "### タグのない地点への追加照合（福岡市周辺の座標つき Wikidata 項目）", "",
              f"- 外枠内の座標つき項目：{len(items)}件（ラベルなしを除く）、すでにタグで結べた項目を除いた照合候補：{len(cands)}件",
              "- 内訳：" + "、".join(f"{k} {v}" for k, v in kinds.items()),
              f"- 名前一致・一部一致で結べそうな地点：{len(cand_spots)}件（人が確認する前提。近接のみは参考）", "",
              "※ Wikidata は CC0。件数は取り込みの可否の目安で、取り込みはしていない。"]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with open(out / "linked.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["spot_id", "地点名", "種別", "Q", "ラベル"] + [PROPS[p] for p in PROPS] + ["撮影作品数"])
        for sid, qs in linked.items():
            for q in sorted(qs):
                e = ents.get(q, {"label": "", "props": set()})
                w.writerow([sid, by_id[sid]["name"], by_id[sid]["category"], q, e["label"]]
                           + ["○" if p in e["props"] else "" for p in PROPS] + [len(film.get(q, []))])
    with open(out / "candidates.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["q", "label", "inst", "kind", "dist_m", "spot_id", "spot_name", "category", "osm"])
        w.writeheader(); w.writerows(sorted(cands, key=lambda c: ({"名前一致": 0, "名前の一部一致": 1, "近接のみ": 2}[c["kind"]], c["dist_m"])))
    # OSM へ wikidata タグを返す案（人が確認してから手で編集する前提。名前一致・150m以内・OSM要素が1つに決まるものだけ）
    prop = [c for c in cands if c["kind"] == "名前一致" and c["dist_m"] <= 150 and len(c["osm"].split()) == 1]
    with open(out / "osm_wikidata_proposal.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["OSM要素", "追加する wikidata", "Wikidataの名前", "地点名", "距離m", "Wikidataの分類"])
        for c in sorted(prop, key=lambda c: c["osm"]):
            w.writerow([c["osm"], c["q"], c["label"], c["spot_name"], c["dist_m"], c["inst"]])
    print("\n".join(lines), f"\nOSMへ返す案：{len(prop)}件")


if __name__ == "__main__":
    main()
