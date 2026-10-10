"""旧版（legacy スキーマ）から、人が手を入れたデータを新しい地点に引き継ぐ

  python tools/import_legacy.py --dry-run   # 何がどの地点に対応するかを表示するだけ
  python tools/import_legacy.py             # 引き継ぐ

対象
- 確認済み・ロケハン済み・資料確認済み・現地確認済みの地点：状態・確度・説明・人のタグを、
  新しい地点（同じ名前で500m以内の最も近いもの、なければ同じ種別で30m以内）に写す。
  （旧版は範囲の中心、新版は範囲の内側の代表点を位置にしているため、大きな公園などは位置が離れることがある）見つからなければ手入力の地点として作る
- 保存した流れ（おすすめに保存・手動作成）：地点を新しい地点に置き換えて保存し直す（置き換えられなければ報告のみ）
- 変化リスク：対応する地点に写す
何度実行しても二重には入らない（引き継ぎ済みの印を sources に残す）。
"""
import argparse
import json
import os
import sys
import unicodedata
import re

import psycopg
from psycopg.rows import dict_row


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"[\s・･\-‐ー－()（）]", "", s).lower()


def find_new(conn, old):
    rows = conn.execute(
        """select id, name, category, ST_Distance(geom, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography) d
             from spots where data_status <> 'archived'
              and ST_DWithin(geom, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, 500)
            order by d""", (old["lon"], old["lat"], old["lon"], old["lat"])).fetchall()
    for r in rows:
        if old["name"] and not old["name"].startswith("（無名）") and norm(r["name"]) == norm(old["name"]):
            return r
    for r in rows:
        if r["category"] == old["category"] and r["d"] <= 30:
            return r
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    if not conn.execute("select 1 from information_schema.tables where table_schema='legacy' and table_name='spots'").fetchone():
        print("旧版のデータ（legacy）がありません"); return 0

    mapping, made = {}, 0
    olds = conn.execute(
        """select s.id, s.code, s.name, s.category, s.status, s.confidence, s.summary, s.era_feel, s.shoot_scope,
                  ST_X(s.geom::geometry) lon, ST_Y(s.geom::geometry) lat,
                  (select array_agg(tag_code) from legacy.spot_tags t where t.spot_id = s.id and t.origin = 'human') tags
             from legacy.spots s
            where s.status in ('scouted','verified') or s.confidence in ('desk_checked','field_checked')
               or exists (select 1 from legacy.spot_tags t where t.spot_id = s.id and t.origin = 'human')""").fetchall()
    print(f"人が手を入れた旧地点: {len(olds)}")
    for o in olds:
        mark = f"legacy:{o['id']}"
        done = conn.execute("select spot_id from sources where kind='legacy' and title=%s", (mark,)).fetchone()
        if done:
            mapping[o["id"]] = done["spot_id"]; continue
        n = find_new(conn, o)
        cat_ok = conn.execute("select 1 from spot_categories where code=%s", (o["category"],)).fetchone()
        if n:
            print(f"  {o['code']} {o['name']} → 新 #{n['id']} {n['name']}（{round(n['d'])}m）")
            sid = n["id"]
        else:
            print(f"  {o['code']} {o['name']} → 対応なし：手入力の地点として作成")
            if a.dry_run:
                continue
            area = conn.execute("""select code from areas where ST_Covers(geom, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography)""",
                                (o["lon"], o["lat"])).fetchone()
            sid = conn.execute(
                """insert into spots (code, name, area_code, geom, category, origin, position_kind)
                   values (%s,%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, %s, 'manual', 'manual') returning id""",
                ("L-" + o["code"], o["name"], area["code"] if area else None, o["lon"], o["lat"],
                 o["category"] if cat_ok else "heritage")).fetchone()["id"]
            made += 1
        if a.dry_run:
            continue
        conn.execute("""update spots set status=%s, confidence=%s, summary=coalesce(%s, summary),
                          era_feel=coalesce(%s, era_feel), shoot_scope=coalesce(%s, shoot_scope) where id=%s""",
                     (o["status"] if o["status"] != "archived" else "candidate", o["confidence"], o["summary"],
                      o["era_feel"], o["shoot_scope"], sid))
        for t in o["tags"] or []:
            conn.execute("""insert into spot_tags (spot_id, tag_code, origin, score) values (%s,%s,'human',1)
                            on conflict (spot_id, tag_code) do update set origin='human', score=1""", (sid, t))
        conn.execute("insert into sources (spot_id, kind, title, note) values (%s,'legacy',%s,%s)",
                     (sid, mark, f"旧版 {o['code']} から引き継ぎ"))
        conn.execute("insert into spot_history (spot_id, actor, event, detail) values (%s,'import','imported',%s)",
                     (sid, json.dumps({"legacy": o["code"]}, ensure_ascii=False)))
        mapping[o["id"]] = sid

    # 保存した流れ
    flows = conn.execute("""select c.* from legacy.clusters c
                             where c.status in ('reviewed','verified') or c.origin = 'human'""").fetchall()
    print(f"保存した流れ: {len(flows)}")
    for f in flows:
        legs = conn.execute("""select cs.seq, s.id, s.code, s.name, s.category, ST_X(s.geom::geometry) lon, ST_Y(s.geom::geometry) lat
                                 from legacy.cluster_spots cs join legacy.spots s on s.id = cs.spot_id
                                where cs.cluster_id = %s order by cs.seq""", (f["id"],)).fetchall()
        new_ids = []
        for l in legs:
            nid = mapping.get(l["id"]) or (find_new(conn, l) or {}).get("id")
            new_ids.append(nid)
        label = " → ".join(l["name"] for l in legs)
        if None in new_ids or not new_ids:
            print(f"  ！置き換えできない地点あり：{label}"); continue
        key = "-".join(str(i) for i in new_ids)
        print(f"  {label} → {key}")
        if a.dry_run or conn.execute("select 1 from clusters where route_key=%s", (key,)).fetchone():
            continue
        cid = conn.execute(
            """insert into clusters (code, name, area_code, transition_score, transition_label, total_minutes, travel_mode,
                                     origin, status, route_key, hit_count, last_hit_at, saved_by, note)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            ("L-" + (f["code"] or str(f["id"])), f["name"], f["area_code"], f["transition_score"], f["transition_label"],
             f["total_minutes"], f["travel_mode"], f["origin"] if f["origin"] in ("human", "search") else "human",
             f["status"], key, f["hit_count"], f["last_hit_at"], f["saved_by"], "旧版から引き継ぎ")).fetchone()["id"]
        for i, nid in enumerate(new_ids):
            conn.execute("""insert into cluster_spots (cluster_id, seq, spot_id, minutes_from_prev, meters_from_prev)
                            values (%s,%s,%s,%s,%s)""", (cid, i, nid, legs[i].get("minutes_from_prev"), None))

    # 変化リスク
    risks = conn.execute("""select r.*, s.name, s.category, ST_X(s.geom::geometry) lon, ST_Y(s.geom::geometry) lat
                              from legacy.change_risks r join legacy.spots s on s.id = r.spot_id
                             where r.risk_type <> 'none'""").fetchall()
    print(f"変化リスク: {len(risks)}")
    for r in risks:
        nid = mapping.get(r["spot_id"]) or (find_new(conn, r) or {}).get("id")
        if not nid or a.dry_run:
            continue
        if not conn.execute("select 1 from change_risks where spot_id=%s and source_url is not distinct from %s and risk_type=%s",
                            (nid, r["source_url"], r["risk_type"])).fetchone():
            conn.execute("""insert into change_risks (spot_id, risk_type, evidence, source_url, verified, detected_at, review_by)
                            values (%s,%s,%s,%s,%s,%s,%s)""",
                         (nid, r["risk_type"], r["evidence"], r["source_url"], r["verified"], r["detected_at"], r["review_by"]))
    if a.dry_run:
        conn.rollback(); print("（試しのため変更していません）")
    else:
        conn.commit(); print(f"引き継ぎました（手入力として作成 {made}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
