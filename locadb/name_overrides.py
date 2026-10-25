"""日本語名の上書き：人が直した name:ja（OSMChange 形式のファイル）を読み、地点名に反映する

  - 取り込み（tools/name_ja_import.py）：ファイルの内容を feature_name_overrides に保存し、いまの地点名も直す
  - 毎週の自動収集（pipeline）：OSM に name:ja が無い要素だけ、この表の名前を name:ja として使う（OSM に name:ja が付けばそちらが優先）
"""
import xml.etree.ElementTree as ET

from .categories import display_name

# 値にこれが入っているものは、人が確かめる途中の印なので取り込まない
PENDING_MARKERS = ("要候補照合", "要確認", "TODO")
TYPE_PREFIX = {"node": "n", "way": "w", "relation": "r"}


def parse_osc(path_or_text) -> tuple[list[dict], list[dict]]:
    """OSMChange の modify/create から name:ja を取り出す。返り値 (使うもの, 取り込まないもの)。
    使うもの：{key, name_ja, name}。取り込まないもの：{key, name_ja, reason}"""
    text = str(path_or_text)
    root = ET.fromstring(text) if text.lstrip().startswith("<") else ET.parse(text).getroot()
    ok, skipped, seen = [], [], set()
    for block in root:
        if block.tag not in ("modify", "create"):
            continue
        for el in block:
            prefix = TYPE_PREFIX.get(el.tag)
            if not prefix:
                continue
            tags = {t.get("k"): t.get("v") for t in el.findall("tag")}
            v = (tags.get("name:ja") or "").strip()
            key = f"{prefix}{el.get('id')}"
            if not v or key in seen:
                continue
            seen.add(key)
            if any(m in v for m in PENDING_MARKERS):
                skipped.append({"key": key, "name_ja": v, "reason": "確認の途中の印がある"})
            else:
                ok.append({"key": key, "name_ja": v, "name": tags.get("name") or ""})
    return ok, skipped


def apply_to_snapshot(conn, snap) -> int:
    """毎週の自動収集：OSM に name:ja が無い要素に、保存してある日本語名を name:ja として与える（地点名もそれに合わせる）"""
    ov = {r["feature_key"]: r["name_ja"] for r in conn.execute("select feature_key, name_ja from feature_name_overrides")}
    n = 0
    for f in snap.points:
        v = ov.get(f.key)
        if v and not f.tags.get("name:ja"):
            f.tags = {**f.tags, "name:ja": v}
            f.name = display_name(f.tags, f.cats[0])
            n += 1
    return n
