"""シーン要件（脚本の文章）から候補クラスターを逆引きする

使い方:
  python -m locadb.search_scene "30代の男性が深夜、一人で歩く。都会だが少し寂しい。歩いているうちに海が見える"
"""
import argparse
import json
import os

import anthropic

from .db import connect

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")


def interpret(conn, scene: str) -> dict:
    tags = conn.execute("select code, name from tags").fetchall()
    groups = conn.execute("select code, name from landscape_groups").fetchall()
    prompt = f"""映像のシーン要件をロケ地検索条件に変換してください。JSONのみ出力。

# シーン
{scene}

# 景観グループ
{json.dumps(groups, ensure_ascii=False)}

# タグ
{json.dumps(tags, ensure_ascii=False)}

# 出力形式
{{"sequence": ["commercial", "water"],      // シーン内で景観が移り変わる順（1〜4個）
  "tags": {{"lonely": 1.0, "night_ok": 0.8}}, // 重み0〜1
  "travel_mode": "walk|car|any",
  "notes": "解釈の要点を1文で"}}"""
    msg = anthropic.Anthropic().messages.create(
        model=MODEL, max_tokens=1500, messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in msg.content if b.type == "text")
    return json.loads(text[text.find("{"): text.rfind("}") + 1])


def sequence_match(seq: list[str], groups: list[str]) -> float:
    """要件の景観順序がクラスター内に順番どおり現れる割合"""
    i = 0
    for g in groups:
        if i < len(seq) and g == seq[i]:
            i += 1
    return i / len(seq) if seq else 0.0


def search(scene: str, area: str | None = None, top: int = 5) -> dict:
    with connect() as conn:
        q = interpret(conn, scene)
        rows = conn.execute(
            """select c.id, c.code, c.name, c.transition_label, c.transition_score, c.total_minutes,
                      c.travel_mode, c.status,
                      json_agg(json_build_object(
                        'name', s.name, 'group', sc.landscape_group, 'confidence', s.confidence,
                        'tags', (select coalesce(json_object_agg(t.tag_code, t.score), '{}')
                                 from spot_tags t where t.spot_id = s.id),
                        'risk', (select bool_or(true) from change_risks r where r.spot_id = s.id
                                 and r.risk_type <> 'none'))
                        order by cs.seq) spots
               from clusters c
               join cluster_spots cs on cs.cluster_id = c.id
               join spots s on s.id = cs.spot_id
               join spot_categories sc on sc.code = s.category
               where c.status <> 'archived' and (%s::text is null or c.area_code = %s)
                 and (%s = 'any' or c.travel_mode = %s)
               group by c.id""",
            (area, area, q.get("travel_mode", "any"), q.get("travel_mode", "any")),
        ).fetchall()

    want = q.get("tags", {})
    results = []
    for r in rows:
        tag_hit = 0.0
        for s in r["spots"]:
            for code, w in want.items():
                tag_hit += w * float((s["tags"] or {}).get(code, 0))
        tag_score = tag_hit / (sum(want.values()) or 1)
        seq = sequence_match(q.get("sequence", []), [s["group"] for s in r["spots"]])
        total = 0.45 * seq + 0.40 * min(tag_score, 1.0) + 0.15 * min((r["transition_score"] or 0) / 3, 1.0)
        results.append({**r, "match": round(total, 3), "seq_match": seq, "tag_score": round(tag_score, 2)})
    results.sort(key=lambda x: x["match"], reverse=True)
    return {"query": q, "results": results[:top]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene")
    ap.add_argument("--area")
    ap.add_argument("--top", type=int, default=5)
    a = ap.parse_args()
    out = search(a.scene, a.area, a.top)
    print("解釈:", out["query"].get("notes"))
    for i, r in enumerate(out["results"], 1):
        route = " → ".join(
            s["name"] + ("⚠" if s["risk"] else "") + ("" if s["confidence"] == "field_checked" else "（未確認）")
            for s in r["spots"])
        print(f"\n{i}. [{r['code']}] 適合 {r['match']}  {r['travel_mode']} {r['total_minutes']}分")
        print(f"   {r['transition_label']}")
        print(f"   {route}")
    print("\n※ 候補は組み合わせの提案であり、撮影可能性を保証するものではありません。")


if __name__ == "__main__":
    main()
