"""写真候補の採否（人が決めたもの）の傾向を調べる（読み取りだけ。DBへは SELECT のみ）

  python tools/photo_decision_analysis.py --out DIR
採用・不採用が決まった候補について、由来・距離・名前一致・種別・説明文の語・ファイル名の型・同じ地点の候補の数などごとに、
採用率を出す。件数が少ない区分は参考値（最小件数未満は「少数」と表示）。
"""
import argparse
import collections
import csv
import re
from pathlib import Path

from locadb.db import connect

SQL = """
select c.id, c.spot_id, c.source, c.file_title, c.description, c.dist_m, c.name_match, c.license, c.status, c.shot_at,
       s.category, s.name as spot_name, sc.landscape_group,
       (select count(*) from spot_photo_candidates x where x.spot_id = c.spot_id) as n_cands
  from spot_photo_candidates c
  join spots s on s.id = c.spot_id join spot_categories sc on sc.code = s.category
 where c.status in ('adopted','rejected')
"""
MIN_N = 8


def dist_bucket(d):
    if d is None: return "不明"
    return "〜10m" if d <= 10 else "〜30m" if d <= 30 else "〜60m" if d <= 60 else "60m〜"


def title_kind(t):
    t = t or ""
    if re.search(r"(IMG|DSC|DSCN|PXL|P\d{6,}|\d{8})[_ -]?\d*\.(jpe?g|png)", t, re.I): return "カメラの連番の名前"
    if re.search(r"[ぁ-んァ-ヶ一-龥]", t): return "日本語の名前"
    return "英語などの名前"


def desc_flags(d):
    d = d or ""
    out = []
    if re.search(r"駅名標|看板|標識|案内|sign|plaque|map", d, re.I): out.append("看板・標識・地図")
    if re.search(r"内部|室内|interior|inside", d, re.I): out.append("内部")
    if re.search(r"夜|night|illumin|ライトアップ", d, re.I): out.append("夜")
    if re.search(r"空撮|aerial|drone", d, re.I): out.append("空撮")
    if not d.strip(): out.append("説明なし")
    return out or ["その他の説明"]


def table(title, groups):
    lines = [f"### {title}", "| 区分 | 採用 | 不採用 | 採用率 |", "|---|---|---|---|"]
    for k, (a, r) in sorted(groups.items(), key=lambda kv: -(kv[1][0] + kv[1][1])):
        n = a + r
        lines.append(f"| {k} | {a} | {r} | {a / n:.0%}{'（少数）' if n < MIN_N else ''} |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="photo-analysis-out"); a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        rows = conn.execute(SQL).fetchall()
        undecided = conn.execute("select count(*) n from spot_photo_candidates where status='candidate'").fetchone()["n"]
    ad = sum(1 for r in rows if r["status"] == "adopted")
    md = [f"決まった候補：{len(rows)}件（採用 {ad}・不採用 {len(rows) - ad}）／未決：{undecided}件\n"]
    def agg(keyf, multi=False):
        g = collections.defaultdict(lambda: [0, 0])
        for r in rows:
            for k in (keyf(r) if multi else [keyf(r)]):
                g[k][0 if r["status"] == "adopted" else 1] += 1
        return g
    md.append(table("由来", agg(lambda r: r["source"])))
    md.append(table("名前が一致", agg(lambda r: "一致" if r["name_match"] else "不一致")))
    md.append(table("地点からの距離", agg(lambda r: dist_bucket(r["dist_m"]))))
    md.append(table("景観グループ", agg(lambda r: r["landscape_group"])))
    md.append(table("種別（上位）", dict(sorted(agg(lambda r: r["category"]).items(), key=lambda kv: -sum(kv[1]))[:15])))
    md.append(table("ファイル名の型", agg(lambda r: title_kind(r["file_title"]))))
    md.append(table("説明文の特徴", agg(lambda r: desc_flags(r["description"]), multi=True)))
    md.append(table("同じ地点の候補の数", agg(lambda r: "1件" if r["n_cands"] == 1 else "2件" if r["n_cands"] == 2 else "3件〜")))
    md.append(table("ライセンス", agg(lambda r: re.sub(r"\s*\d(\.\d)?.*$", "", r["license"] or ""))))
    (out / "summary.md").write_text("\n".join(md), encoding="utf-8")
    with open(out / "decided.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["結果", "由来", "地点", "種別", "ファイル", "距離m", "名前一致", "説明"])
        for r in rows:
            w.writerow([r["status"], r["source"], r["spot_name"], r["category"], r["file_title"], round(r["dist_m"] or 0),
                        "○" if r["name_match"] else "", (r["description"] or "")[:80]])
    print("\n".join(md))


if __name__ == "__main__":
    main()
