"""一括実行: 収集 → 統合 → AI分類 → クラスター生成 → 変化リスク監視

  python -m locadb.pipeline --area fuk-chuo            # 1区
  python -m locadb.pipeline --all                       # 福岡市7区
  python -m locadb.pipeline --area fuk-chuo --skip-collect
"""
import argparse

from . import build_clusters, classify, collect_osm, merge, monitor_risk

ALL_AREAS = list(collect_osm.AREA_NAMES)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", action="append")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--skip-collect", action="store_true")
    ap.add_argument("--skip-classify", action="store_true")
    a = ap.parse_args()
    areas = ALL_AREAS if a.all else (a.area or ["fuk-chuo"])
    for area in areas:
        print(f"=== {area} ===")
        if not a.skip_collect:
            print("[1] OSM収集");      collect_osm.run(area)
        print("[2] 重複統合");          merge.run(area)
        if not a.skip_classify:
            print("[3] AI分類");       classify.run(area, limit=1000)
        print("[4] クラスター生成");    build_clusters.run(area)
    print("[5] 変化リスク監視");        monitor_risk.run()


if __name__ == "__main__":
    main()
