#!/usr/bin/env python3
"""Wikipedia(ja)の冒頭文から「事実の候補」を集める。1秒間隔・直列。DBには触れない。"""
import json, re, sys, time, urllib.parse, urllib.request

UA = "FukuokaLocaDB/1.0 (https://github.com/mandrillfuk-dev/fukuoka-locadb)"
SRC, OUT, HAVE = sys.argv[1], sys.argv[2], sys.argv[3]
last = [0.0]

def get(url):
    wait = 1.0 - (time.time() - last[0])
    if wait > 0: time.sleep(wait)
    last[0] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

rows = []
for l in open(SRC, encoding="utf-8"):
    if l.startswith("|") and not l.startswith("| id") and not l.startswith("| --"):
        c = [x.strip() for x in l.strip().strip("|").split("|")]
        rows.append(c)
have = set(int(x) for x in open(HAVE).read().split("\n")[1].split(","))

# 1) wikidata -> jawiki タイトル（タグにjaがない地点）
need = {}
for sid, name, wp, wd in rows:
    if not wp.startswith("ja:") and wd.startswith("Q"):
        need[wd] = None
qs = list(need)
for i in range(0, len(qs), 50):
    ids = "|".join(qs[i:i+50])
    try:
        d = get("https://www.wikidata.org/w/api.php?action=wbgetentities&props=sitelinks&sitefilter=jawiki&format=json&ids=" + ids)
    except Exception as e:
        print("wikidata 失敗（スキップ）:", e, file=sys.stderr, flush=True)
        break
    for q, e in d.get("entities", {}).items():
        t = e.get("sitelinks", {}).get("jawiki", {}).get("title")
        need[q] = t
    print("wikidata", i, file=sys.stderr, flush=True)

EXCL = re.compile(r"事故|死亡|死者|殺|自殺|逮捕|火災|爆発|墜落|災害|遭難|被害|訴訟|不祥事")
DIRW = re.compile(r"(北|南|東|西)(北|南|東|西)?(方向|方角|へ向|に向か)")
HIST = re.compile(r"\d{3,4}年|創建|創立|設立|開業|開校|開設|開園|建立|築造|移転|改称|由来|起源|時代")
def core(n):
    n = re.sub(r"[（(].*?[）)]", "", n)
    n = re.sub(r"^(福岡市立|福岡県立|市立|県立|私立|公立)\s*", "", n)
    return n.strip()

out = []
log = open(OUT + ".log", "w", encoding="utf-8")
for k, (sid, name, wp, wd) in enumerate(rows):
    if wp.startswith("ja:"): title, how = wp[3:], "タグ"
    elif wd in need and need[wd]: title, how = need[wd], "wikidata"
    else:
        log.write(f"{sid}\t{name}\t記事なし\n"); continue
    url = ("https://ja.wikipedia.org/w/api.php?action=query&prop=extracts|pageprops&exintro=1&explaintext=1"
           "&redirects=1&ppprop=disambiguation&format=json&titles=" + urllib.parse.quote(title))
    try:
        d = get(url)
    except Exception as e:
        log.write(f"{sid}\t{name}\t取得失敗 {e}\n"); continue
    pages = d.get("query", {}).get("pages", {})
    pg = next(iter(pages.values()), {})
    ext = (pg.get("extract") or "").strip()
    rtitle = pg.get("title", title)
    if "pageprops" in pg or not ext:
        log.write(f"{sid}\t{name}\t曖昧さ回避または空 {rtitle}\n"); continue
    src = "https://ja.wikipedia.org/wiki/" + urllib.parse.quote(rtitle.replace(" ", "_"), safe="()_")
    sents = [s.strip() + "。" for s in re.split(r"。", re.sub(r"\s*\n+\s*", " ", ext)) if s.strip()]
    nm_ok = core(name)[:6] in (rtitle + sents[0]) if sents else False
    covered = int(sid) in have
    for no, s in enumerate(sents[:9], 1):
        if no == 1 and covered: continue          # 所在の文は承認済み
        kind = "location" if no == 1 else ("history" if HIST.search(s) else "feature")
        if EXCL.search(s) or DIRW.search(s): mark, note = "不採", "除外語"
        elif not nm_ok: mark, note = "保留", "名前不一致の疑い（記事：%s）" % rtitle
        else: mark, note = "", ""
        if len(s) > 220 and not note: note = "長文"
        out.append([sid, name, str(no), kind, s, src, mark, note, "あり" if covered else "なし", how])
    if k % 25 == 0: print(k, len(rows), file=sys.stderr, flush=True)

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\t".join(["地点ID","地点名","候補No","種類","候補の文","出典URL","採否（採/不採/保留）","備考","既存材料","取得経路"]) + "\n")
    for r in out: f.write("\t".join(x.replace("\t"," ") for x in r) + "\n")
print("done", len(out), file=sys.stderr)
