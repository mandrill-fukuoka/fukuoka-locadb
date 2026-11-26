"""昼の人の多さ：e-Stat の「平成28年経済センサス-活動調査 地域メッシュ統計（500m）」から、福岡市の範囲の全産業の従業者数を取り込む（DB変更294。AIなし・費用ゼロ）

  python -m locadb.estat_workers --dry-run [--out DIR]   読み取りのみ。メッシュの数・合計・上位だけ出す
  python -m locadb.estat_workers --apply                 workplace_mesh を作り直し、daytime_refresh と vibe_apply を流す

- 政府統計の総合窓口（e-Stat）の API（appId は環境変数 ESTAT_APP_ID。GitHub Actions の Secrets だけに置く）。
  appId は URL に入るので、例外・ログには URL を出さない（エラーは状態番号と型名だけ）。
- 表：統計表 8003006559（1次メッシュ M5030＝福岡県の大半を含む）の「従業者数＿Ａ～Ｒ全産業（Ｓ公務を除く）」。値が秘匿（X・-）のメッシュは数えない。
- 500m メッシュコード（9桁）は、中心の緯度経度に直して使う。福岡市の範囲の箱だけを保存する。
- 調査は2016年（平成28年）。令和3年の表は API の一覧に出ていない。出たら TABLE_ID と SURVEY_YEAR を差し替える。
- 画面の出典：政府統計の総合窓口（e-Stat）（NOTICE.md に記載。クレジットの文面は e-Stat の規約による）。
"""
import argparse
import json
import os
import sys
from pathlib import Path

import requests

from .db import connect, job

API = "https://api.e-stat.go.jp/rest/3.0/app/json"
TABLE_ID, SURVEY_YEAR = "8003006559", 2016
BBOX = (130.20, 33.43, 130.52, 33.72)           # lon_min, lat_min, lon_max, lat_max（station_flow と同じ箱）
MIN_MESHES = 800                                 # これ未満なら取得の失敗とみる


def mesh_center(code: str) -> tuple[float, float]:
    """500m メッシュコード（9桁）→ 中心の（経度, 緯度）。1次4桁＋2次2桁＋3次2桁＋1桁（1=南西 2=南東 3=北西 4=北東）"""
    if len(code) != 9 or not code.isdigit():
        raise ValueError(f"500mメッシュコードではない: {code}")
    p, u = int(code[0:2]), int(code[2:4])
    r, s = int(code[4]), int(code[5])
    t, v = int(code[6]), int(code[7])
    q = int(code[8])
    if not 1 <= q <= 4:
        raise ValueError(f"500mメッシュコードではない: {code}")
    lat = p / 1.5 + r * (5 / 60) + t * (30 / 3600) + (15 / 3600 if q in (3, 4) else 0) + (7.5 / 3600)
    lon = 100 + u + s * (7.5 / 60) + v * (45 / 3600) + (22.5 / 3600 if q in (2, 4) else 0) + (11.25 / 3600)
    return lon, lat


def _get(path: str, params: dict, app_id: str) -> dict:
    try:
        r = requests.get(f"{API}/{path}", params={"appId": app_id, "lang": "J", **params}, timeout=120)
    except Exception as e:                                   # 例外の文に URL（appId）が入るので型名だけ
        raise RuntimeError(f"e-Stat への接続に失敗（{type(e).__name__}）") from None
    if r.status_code != 200:
        raise RuntimeError(f"e-Stat が状態 {r.status_code} を返した")
    try:
        return r.json()
    except ValueError:
        raise RuntimeError("e-Stat の返事が JSON ではない") from None


def workers_code(meta: dict) -> str:
    """分類（cat01）から「従業者数＿Ａ～Ｒ全産業」のコードを選ぶ"""
    objs = ((meta.get("GET_META_INFO") or {}).get("METADATA_INF") or {}).get("CLASS_INF", {}).get("CLASS_OBJ") or []
    for o in objs:
        if o.get("@id") != "cat01":
            continue
        cl = o.get("CLASS") or []
        cl = [cl] if isinstance(cl, dict) else cl
        for c in cl:
            name = c.get("@name", "")
            if name.startswith("従業者数") and "全産業" in name:
                return c["@code"]
    raise RuntimeError("従業者数（全産業）の分類が見つからない")


def parse(values: list[dict]) -> list[dict]:
    """値の一覧 → 箱の中のメッシュだけ [{mesh_code, workers, lon, lat}]。秘匿・数でない値は除く"""
    out = []
    for v in values:
        raw = str(v.get("$", "")).strip()
        if not raw.isdigit():
            continue
        code = v.get("@area", "")
        try:
            lon, lat = mesh_center(code)
        except ValueError:
            continue
        if not (BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]):
            continue
        out.append({"mesh_code": code, "workers": int(raw), "lon": lon, "lat": lat})
    return sorted(out, key=lambda r: -r["workers"])


def fetch(app_id: str) -> list[dict]:
    code = workers_code(_get("getMetaInfo", {"statsDataId": TABLE_ID}, app_id))
    values, start = [], 1
    while True:
        js = _get("getStatsData", {"statsDataId": TABLE_ID, "cdCat01": code, "metaGetFlg": "N",
                                   "limit": "100000", "startPosition": str(start)}, app_id)
        st = (js.get("GET_STATS_DATA") or {}).get("STATISTICAL_DATA") or {}
        vs = (st.get("DATA_INF") or {}).get("VALUE") or []
        values += [vs] if isinstance(vs, dict) else vs
        nxt = (st.get("RESULT_INF") or {}).get("NEXT_KEY")
        if not nxt:
            return parse(values)
        start = int(nxt)


def replace_meshes(conn, rows: list[dict], source: str) -> int:
    with conn.transaction():
        conn.execute("delete from workplace_mesh")
        for r in rows:
            conn.execute("insert into workplace_mesh (mesh_code, workers, survey_year, geom, source) "
                         "values (%s,%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, %s)",
                         (r["mesh_code"], r["workers"], SURVEY_YEAR, r["lon"], r["lat"], source))
    return len(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default="estat-out")
    a = ap.parse_args(argv)
    app_id = os.environ.get("ESTAT_APP_ID", "").strip()
    if not app_id:
        raise SystemExit("ESTAT_APP_ID が未登録です（GitHub の Secrets）")
    rows = fetch(app_id)
    source = f"e-Stat {TABLE_ID}"
    stats = {"dry_run": a.dry_run, "source": source, "survey_year": SURVEY_YEAR, "meshes": len(rows),
             "workers_total": sum(r["workers"] for r in rows),
             "top": [(r["mesh_code"], r["workers"], round(r["lat"], 4), round(r["lon"], 4)) for r in rows[:8]]}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    with connect() as conn, job(conn, "estat_workers") as st:
        before = conn.execute("select count(*) n from workplace_mesh").fetchone()["n"]
        stats["before"] = before
        if len(rows) < MIN_MESHES:
            raise SystemExit(f"取り込むメッシュの数（{len(rows)}）が少なすぎるので止めました（取得の失敗かも。下限 {MIN_MESHES}）")
        if a.apply and before and len(rows) < before * 0.7:
            raise SystemExit(f"取り込むメッシュの数（{len(rows)}）が今（{before}）の7割未満なので止めました")
        if a.apply:
            replace_meshes(conn, rows, source)
            stats["spots_refreshed"] = conn.execute("select daytime_refresh() n").fetchone()["n"]
            stats["vibe"] = {k: v for k, v in conn.execute("select vibe_apply() v").fetchone()["v"].items()
                             if k in ("updated", "version")}
            stats.update({k: v for k, v in conn.execute("select daytime_stats() d").fetchone()["d"].items()
                          if k.endswith("p10_50_90_99") or k.startswith("spots_with")})
        st.update(stats)
    (Path(a.out) / "estat.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
