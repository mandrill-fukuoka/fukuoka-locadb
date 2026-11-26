"""e-Stat API で、経済センサスの地域メッシュ統計（従業者数）の表を探す。読み取りだけ。DBは触らない。
appId は環境変数 ESTAT_APP_ID からだけ読む（GitHub Actions の Secrets）。URL・例外メッセージには appId が入るので、
出力には一切出さない（例外は型名だけ）。"""
import json
import os
import sys

import requests

API = "https://api.e-stat.go.jp/rest/3.0/app/json"
QUERIES = [
    {"searchWord": "経済センサス 従業者数 メッシュ", "searchKind": "2"},
    {"searchWord": "経済センサス 活動調査 500mメッシュ", "searchKind": "2"},
    {"searchWord": "経済センサス 基礎調査 1kmメッシュ 従業者", "searchKind": "2"},
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
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
