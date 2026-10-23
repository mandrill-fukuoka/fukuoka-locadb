"""「別の場所」の写真の手がかり調べ（読み取りだけ。SELECT のみ）

  python -m tools.photo_misplaced_probe --out DIR
不採用（別の場所）の写真と、不採用の見込み高の未決の写真について、ファイル名・説明文に「近くの別の地点」の名前が入っているかを調べる。
入っていれば、その写真は別の地点のものである可能性が高い（移す先の手がかり）。
"""
import argparse
import csv
import collections
from pathlib import Path

from locadb import commons_photos as cp
from locadb.db import connect

CANDS = """
select c.id, c.spot_id, c.status, c.reject_reason, c.source, c.file_title, c.description, c.dist_m,
       s.name as spot_name, s.category, photo_suggest(c.source, c.name_match, c.description, c.file_title, s.category, c.dist_m::numeric) as suggest
  from spot_photo_candidates c join spots s on s.id = c.spot_id
 where c.source = 'commons' and ((c.status = 'rejected' and c.reject_reason = 'wrong_place') or c.status = 'candidate')
"""
NEAR = """
select t.id, t.code, t.name, t.category
  from spots t, spots s
 where s.id = %s and t.id <> s.id and t.data_status <> 'archived' and t.name is not null
   and st_dwithin(t.geom, s.geom, %s)
"""


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="photo-misplaced-out"); ap.add_argument("--radius", type=float, default=800)
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    rows = []
    with connect() as conn:
        cands = conn.execute(CANDS).fetchall()
        for c in cands:
            text = cp.norm(c["file_title"] or "") + " " + cp.norm(c["description"] or "")
            hits = []
            for t in conn.execute(NEAR, (c["spot_id"], a.radius)).fetchall():
                n = cp.norm(t["name"] or "")
                if len(n) >= 3 and n in text:
                    hits.append(t)
            rows.append((c, hits))
    groups = collections.defaultdict(lambda: [0, 0, 0])      # 件数, 手がかりあり, 手がかりが1つに決まる
    for c, h in rows:
        k = "別の場所で不採用（人が判断）" if c["status"] == "rejected" else f"未決・おすすめ度 {c['suggest']}"
        groups[k][0] += 1
        groups[k][1] += 1 if h else 0
        groups[k][2] += 1 if len(h) == 1 else 0
    md = [f"近くの別の地点（{a.radius:.0f}m 以内）の名前が、ファイル名・説明文に入っているか\n", "| 区分 | 件数 | 手がかりあり | 移し先が1つに決まる |", "|---|---|---|---|"]
    md += [f"| {k} | {v[0]} | {v[1]} | {v[2]} |" for k, v in sorted(groups.items())]
    (out / "summary.md").write_text("\n".join(md), encoding="utf-8")
    with open(out / "misplaced.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["状態", "おすすめ度", "いまの地点", "ファイル", "説明", "移し先の候補（地点名・コード）"])
        for c, h in rows:
            if h:
                w.writerow([c["status"], c["suggest"], c["spot_name"], c["file_title"], (c["description"] or "")[:60],
                            " / ".join(f"{t['name']}({t['code']})" for t in h)])
    print("\n".join(md))


if __name__ == "__main__":
    main()
