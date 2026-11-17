-- 変化リスクの誤検知の後始末（2026-11-17）
-- 橋・道路・川・線路などは、再開発の記事に「経路の目印」として名前が出るだけで反応していた（天神ビッグバンのページに
-- 西大橋・天神橋・博多大橋）。監視の照合からこれらの種別を外したので（locadb/monitor_risk.py の INFRA_CATEGORIES）、
-- 機械が付けただけで人が確認していない既存の要確認を「該当なし」にする。人が確認済みにしたものは触らない
update change_risks r
   set risk_type = 'none', verified = true,
       evidence = coalesce(r.evidence, '') || E'\n[該当なし] 自動：橋・道路・川などは監視の照合の対象外にした（250）'
  from spots s
 where s.id = r.spot_id and r.verified = false and r.risk_type <> 'none' and r.certainty = 'keyword'
   and s.category in ('bridge_overpass','underpass','expressway','railway','river','riverbank','coast',
                      'breakwater','alley','slope_street');
