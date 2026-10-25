"""日本語名の取り込み：人が直した name:ja（OSMChange 形式のファイル）を、地点名に反映する

  python -m tools.name_ja_import FILE [--apply]
  既定は試し（読み取りのみ。DBは変えない）。--apply で、(1) 上書きの表に保存 (2) 地点名を直す（旧名は別名に残す・履歴に記録）。
  何度実行しても同じ結果（すでに同じ名前の地点は触らない）。
"""
import argparse
import json
from pathlib import Path

from locadb.db import connect
from locadb.name_overrides import parse_osc

FIND = """
select sf.feature_key, s.id, s.code, s.name, s.alt_names, s.origin, s.locked, s.status, s.confidence, s.data_status
  from spot_features sf join spots s on s.id = sf.spot_id
 where sf.feature_key = any(%s) and sf.role = 'primary'
"""


def protected(r) -> bool:
    return r["origin"] == "manual" or r["locked"] or r["status"] in ("scouted", "verified") \
        or r["confidence"] in ("desk_checked", "field_checked")


def run(conn, path, apply: bool, log=print) -> dict:
    ok, skipped = parse_osc(path)
    want = {o["key"]: o for o in ok}
    rows = conn.execute(FIND, (list(want),)).fetchall()
    by_key = {}
    for r in rows:
        by_key.setdefault(r["feature_key"], []).append(r)
    res = {"in_file": len(ok), "skipped": skipped, "no_spot": [], "same": 0, "renamed": 0, "renamed_protected": 0, "examples": []}
    for key, o in want.items():
        spots = by_key.get(key, [])
        if not spots:
            res["no_spot"].append({"key": key, "name_ja": o["name_ja"], "name": o["name"]})
            continue
        for r in spots:
            if r["name"] == o["name_ja"]:
                res["same"] += 1
                continue
            res["renamed"] += 1
            res["renamed_protected"] += 1 if protected(r) else 0
            if len(res["examples"]) < 10:
                res["examples"].append(f"{r['code']}  {r['name']} → {o['name_ja']}" + ("（確認ずみの地点）" if protected(r) else ""))
            if apply:
                alt = [x for x in dict.fromkeys(list(r["alt_names"] or []) + [r["name"]]) if x and x != o["name_ja"]]
                conn.execute("update spots set name=%s, alt_names=%s where id=%s", (o["name_ja"], alt, r["id"]))
                # 確認ずみの地点は「前回見たOSMの値」の名前も合わせる（次の自動収集で「名前が変わった」と要確認にしない）
                conn.execute("""update spots set osm_seen = jsonb_set(osm_seen, '{name}', to_jsonb(%s::text))
                                 where id=%s and osm_seen is not null""", (o["name_ja"], r["id"]))
                conn.execute("insert into spot_history (spot_id, actor, event, detail) values (%s,%s,'renamed',%s)",
                             (r["id"], "name-ja-import", json.dumps({"from": r["name"], "to": o["name_ja"], "source": Path(str(path)).name},
                                                                   ensure_ascii=False)))
    if apply:
        conn.executemany("""insert into feature_name_overrides (feature_key, name_ja, source) values (%s,%s,%s)
                            on conflict (feature_key) do update set name_ja = excluded.name_ja, source = excluded.source""",
                         [(k, o["name_ja"], Path(str(path)).name) for k, o in want.items()])
        conn.commit()
    else:
        conn.rollback()
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("file"); ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    with connect() as conn:
        res = run(conn, a.file, a.apply)
    short = {**res, "no_spot": len(res["no_spot"]), "skipped": res["skipped"]}
    print(json.dumps(short, ensure_ascii=False, indent=1))
    print(f"\n{'反映しました' if a.apply else '試しのため、DBは変えていません'}")
    if res["no_spot"]:
        print("\n地点にならなかったもの（地点として登録されていない要素）：")
        for x in res["no_spot"][:40]:
            print(f"  {x['key']}  {x['name']} → {x['name_ja']}")


if __name__ == "__main__":
    main()
