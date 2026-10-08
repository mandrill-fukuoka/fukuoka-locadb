"""Claude API で地点の時代感・雰囲気タグ・撮影範囲を推定する

推定結果は confidence='ai_estimated'、spot_tags.origin='ai' として登録し、
人が確認するまで「確認済み」とは扱わない。
"""
import argparse
import json
import os

import anthropic

from .db import connect, job

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
BATCH = 20

SYSTEM = """あなたは映像制作のロケーションリサーチャーです。
福岡の地点データ（OSMタグ・周辺施設）から、撮影ロケ地としての特徴を推定します。
- 与えられた情報から妥当に推定できる範囲だけを答え、根拠のない断定はしない
- タグは必ず指定された語彙のコードから選ぶ
- 学校・病院・役所等は shoot_scope を exterior_offsite（敷地外からの外観）とする
- 出力はJSONのみ"""


def load_vocab(conn):
    tags = conn.execute("select code, name, facet from tags order by facet, code").fetchall()
    cats = conn.execute("select code, name from spot_categories").fetchall()
    return tags, cats


def context_for(conn, spot_id: int) -> dict:
    s = conn.execute(
        """select s.id, s.name, s.category, sc.name as category_name,
                  ST_Y(s.geom::geometry) lat, ST_X(s.geom::geometry) lon,
                  (select jsonb_agg(rc.raw->'tags') from raw_candidates rc where rc.spot_id=s.id) osm_tags
           from spots s left join spot_categories sc on sc.code=s.category where s.id=%s""",
        (spot_id,),
    ).fetchone()
    near = conn.execute(
        """select n.name, n.category, round(ST_Distance(n.geom, s.geom)) m
           from spots s join spots n on n.id<>s.id and ST_DWithin(n.geom, s.geom, 400)
           where s.id=%s order by m limit 15""",
        (spot_id,),
    ).fetchall()
    return {"id": s["id"], "name": s["name"], "category": s["category_name"],
            "lat": round(s["lat"], 5), "lon": round(s["lon"], 5),
            "osm_tags": s["osm_tags"], "nearby_400m": near}


def classify_batch(client, tags, cats, items: list[dict]) -> list[dict]:
    vocab = "\n".join(f"{t['code']}: {t['name']} ({t['facet']})" for t in tags)
    cat_list = "\n".join(f"{c['code']}: {c['name']}" for c in cats)
    prompt = f"""以下の地点ごとに推定してください。

# タグ語彙
{vocab}

# 地点種別（現在の種別が不適切なら修正案を category に）
{cat_list}

# 出力形式（JSON配列のみ）
[{{"id": 1, "category": "code", "era_feel": "showa|heisei|reiwa|mixed|historic",
  "shoot_scope": "exterior_public|exterior_offsite|interior_permit|unknown",
  "owner_type": "public|private|mixed|unknown",
  "tags": [{{"code": "lonely", "score": 0.7}}],
  "summary": "撮影目線での40字程度の説明"}}]

# 地点
{json.dumps(items, ensure_ascii=False, default=str)}"""
    msg = client.messages.create(model=MODEL, max_tokens=8000, system=SYSTEM,
                                 messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    text = text[text.find("["): text.rfind("]") + 1]
    return json.loads(text)


def run(area_code: str, limit: int = 200) -> dict:
    client = anthropic.Anthropic()
    with connect() as conn, job(conn, f"classify:{area_code}") as stats:
        tags, cats = load_vocab(conn)
        valid_tags = {t["code"] for t in tags}
        valid_cats = {c["code"] for c in cats}
        ids = [r["id"] for r in conn.execute(
            "select id from spots where area_code=%s and status='candidate' order by id limit %s",
            (area_code, limit)).fetchall()]
        done = 0
        for i in range(0, len(ids), BATCH):
            items = [context_for(conn, sid) for sid in ids[i:i + BATCH]]
            for res in classify_batch(client, tags, cats, items):
                sid = res.get("id")
                if sid not in ids:
                    continue
                conn.execute(
                    """update spots set
                         category = coalesce(%s, category), era_feel=%s, shoot_scope=%s,
                         owner_type=%s, summary=%s, status='ai_tagged', confidence='ai_estimated'
                       where id=%s and status='candidate'""",
                    (res.get("category") if res.get("category") in valid_cats else None,
                     res.get("era_feel"), res.get("shoot_scope", "unknown"),
                     res.get("owner_type", "unknown"), res.get("summary"), sid),
                )
                for t in res.get("tags", []):
                    if t.get("code") in valid_tags:
                        conn.execute(
                            """insert into spot_tags (spot_id, tag_code, origin, score)
                               values (%s,%s,'ai',%s)
                               on conflict (spot_id, tag_code) do update
                                 set score = excluded.score where spot_tags.origin='ai'""",
                            (sid, t["code"], float(t.get("score", 0.5))),
                        )
                done += 1
            conn.commit()
            print(f"  分類済み {done}/{len(ids)}")
        stats["classified"] = done
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="fuk-chuo")
    ap.add_argument("--limit", type=int, default=200)
    a = ap.parse_args()
    run(a.area, a.limit)
