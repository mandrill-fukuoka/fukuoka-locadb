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


ERA = ("showa", "heisei", "reiwa", "mixed", "historic")
SCOPE = ("exterior_public", "exterior_offsite", "interior_permit", "unknown")
OWNER = ("public", "private", "mixed", "unknown")


def pick(v, allowed, default):
    return v if v in allowed else default


def load_vocab(conn):
    tags = conn.execute("select code, name, facet from tags order by facet, code").fetchall()
    cats = conn.execute("select code, name from spot_categories").fetchall()
    return tags, cats


def context_for(conn, spot_id: int) -> dict:
    s = conn.execute(
        """select s.id, s.name, s.category, sc.name as category_name,
                  ST_Y(s.geom::geometry) lat, ST_X(s.geom::geometry) lon,
                  (select jsonb_agg(f.tags) from spot_features sf join features f on f.key = sf.feature_key
                    where sf.spot_id=s.id and f.tags is not null) osm_tags
           from spots s left join spot_categories sc on sc.code=s.category where s.id=%s""",
        (spot_id,),
    ).fetchone()
    near = conn.execute(
        """select n.name, n.category, round(ST_Distance(n.geom, s.geom)) m
           from spots s join spots n on n.id<>s.id and ST_DWithin(n.geom, s.geom, 400) and n.data_status<>'archived'
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

# 出力形式（JSON配列のみ）
[{{"id": 1, "era_feel": "showa|heisei|reiwa|mixed|historic",
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


def run_all(run_id: int | None = None, limit: int = 1500) -> dict:
    return run(None, limit, run_id)


def run(area_code: str | None, limit: int = 200, run_id: int | None = None) -> dict:
    client = anthropic.Anthropic()
    with connect() as conn, job(conn, f"classify:{area_code or 'all'}", run_id) as stats:
        tags, cats = load_vocab(conn)
        valid_tags = {t["code"] for t in tags}
        valid_cats = {c["code"] for c in cats}
        ids = [r["id"] for r in conn.execute(
            """select id from spots where (%s::text is null or area_code=%s) and status='candidate'
                 and data_status <> 'archived' and origin = 'osm' and not locked order by id limit %s""",
            (area_code, area_code, limit)).fetchall()]
        done = 0
        for i in range(0, len(ids), BATCH):
            items = [context_for(conn, sid) for sid in ids[i:i + BATCH]]
            for res in classify_batch(client, tags, cats, items):
                sid = res.get("id")
                if sid not in ids:
                    continue
                conn.execute(
                    """update spots set
                         era_feel=%s, shoot_scope=%s,
                         owner_type=%s, summary=%s, status='ai_tagged', confidence='ai_estimated'
                       where id=%s and status='candidate'""",
                    (pick(res.get("era_feel"), ERA, None), pick(res.get("shoot_scope"), SCOPE, "unknown"),
                     pick(res.get("owner_type"), OWNER, "unknown"), res.get("summary"), sid),
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
