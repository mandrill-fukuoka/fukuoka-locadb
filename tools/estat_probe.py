"""e-Stat API で、経済センサスの地域メッシュ統計（従業者数）の表を探す。読み取りだけ。DBは触らない。
appId は環境変数 ESTAT_APP_ID からだけ読む（GitHub Actions の Secrets）。URL・例外メッセージには appId が入るので、
出力には一切出さない（例外は型名だけ）。"""
import json
import os
import sys

import requests

API = "https://api.e-stat.go.jp/rest/3.0/app/json"
QUERIES = [
    {"searchWord": "経済センサス M5030", "searchKind": "2"},
    {"searchWord": "経済センサス メッシュ", "searchKind": "2", "surveyYears": "2021"},
    {"searchWord": "経済センサス メッシュ", "searchKind": "2", "surveyYears": "2016"},
    {"searchWord": "経済センサス メッシュ 令和3年", "searchKind": "2"},
]


def call(path, params, app_id):
    try:
        r = requests.get(f"{API}/{path}", params={"appId": app_id, "lang": "J", **params}, timeout=60)
        return r.status_code, r.json()
    except Exception as e:      # URL に appId が入るので型名だけ
        return None, {"error": type(e).__name__}


def main():
    app_id = os.environ.get("ESTAT_APP_ID", "").strip()
    out = ["## e-Stat 経済センサス（メッシュ）の表の調査", ""]
    if not app_id:
        out.append("ESTAT_APP_ID が未登録（Secrets）。")
        print("\n".join(out)); return 1
    for q in QUERIES:
        code, js = call("getStatsList", {**q, "limit": "30"}, app_id)
        res = (js.get("GET_STATS_LIST") or {})
        status = (res.get("RESULT") or {}).get("STATUS")
        out.append(f"### {q['searchWord']}  (http={code}, status={status})")
        infos = ((res.get("DATALIST_INF") or {}).get("TABLE_INF")) or []
        if isinstance(infos, dict):
            infos = [infos]
        for t in infos:
            title = t.get("TITLE")
            title = title.get("$") if isinstance(title, dict) else title
            out.append(f"- id={t.get('@id')} 調査年月={t.get('SURVEY_DATE')} 統計={t.get('STATISTICS_NAME')} 表題={title} 範囲={t.get('COLLECT_AREA') or ''}")
        out.append("")
    first = None
    for q in QUERIES:
        code, js = call("getStatsList", {**q, "limit": "5"}, app_id)
        infos = (((js.get("GET_STATS_LIST") or {}).get("DATALIST_INF") or {}).get("TABLE_INF")) or []
        if isinstance(infos, dict):
            infos = [infos]
        if infos:
            first = infos[0].get("@id"); break
    if first:
        code, js = call("getMetaInfo", {"statsDataId": first}, app_id)
        cls = (((js.get("GET_META_INFO") or {}).get("METADATA_INF") or {}).get("CLASS_INF") or {}).get("CLASS_OBJ") or []
        out.append(f"### 先頭の表 {first} の分類（http={code}）")
        for o in cls:
            cl = o.get("CLASS")
            cl = [cl] if isinstance(cl, dict) else (cl or [])
            out.append(f"- {o.get('@id')} {o.get('@name')}: {len(cl)}件 例=" + " / ".join(f"{c.get('@code')}:{c.get('@name')}" for c in cl[:12]))
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
