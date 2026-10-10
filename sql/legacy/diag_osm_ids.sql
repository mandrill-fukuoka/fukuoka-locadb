-- ============================================================
-- 診断（読み取りのみ・データは変更しません）
-- OSM ID の変化や再収集で生じた「取り残し」と「重複候補」を数える
-- Supabase の SQL Editor に全文を貼り付けて Run。結果は1つの表（項目・件数）で表示されます。
-- ============================================================
with last_job as (           -- 区ごとの直近の「成功した」収集と、その回にスキップした種別
  select distinct on (split_part(job, ':', 2))
         split_part(job, ':', 2) as area, started_at,
         coalesce(stats->'skipped', '[]'::jsonb) as skipped
    from job_runs
   where job like 'collect_osm:%' and finished_at is not null and error is null
   order by split_part(job, ':', 2), started_at desc
),
stale_raw as (               -- 直近の収集で返ってこなかった収集データ（OSM側で消えた・IDが変わった・点の数が減った）
  select rc.*
    from raw_candidates rc
    join last_job j on j.area = rc.raw->>'area'
   where rc.source = 'osm'
     and rc.fetched_at < j.started_at
     and not (j.skipped ? rc.category_hint)
),
orphan_spots as (            -- 紐づく収集データが全部「取り残し」になった地点（＝OSMにもう無い可能性）
  select s.id
    from spots s
   where s.status <> 'archived'
     and exists (select 1 from raw_candidates rc where rc.spot_id = s.id)
     and not exists (select 1 from raw_candidates rc
                      where rc.spot_id = s.id and rc.id not in (select id from stale_raw))
),
dup_pairs as (               -- 同じ名前・同じ種別で 300m 以内にある地点の組（川などは間隔の半分未満＝重複の疑い）
  select a.id a_id, b.id b_id, a.name, a.category,
         round(ST_Distance(a.geom, b.geom)) as meters
    from spots a
    join spots b on b.id > a.id and b.name = a.name and b.category = a.category
                and ST_DWithin(a.geom, b.geom, 300)
   where a.status <> 'archived' and b.status <> 'archived'
     and a.name not like '（無名）%'
     and ( a.category not in ('river','coast','railway','shotengai','public_space')
           or ST_Distance(a.geom, b.geom) < case a.category when 'railway' then 300
                                                            when 'river' then 200 when 'coast' then 200 else 75 end )
)
select '収集データ（全体）'                      as 項目, count(*)::text as 件数 from raw_candidates
union all select '└ 直近の収集で返ってこなかったもの', count(*)::text from stale_raw
union all select '└ うち 川などの @番号 付き',        count(*)::text from stale_raw where source_ref like '%@%'
union all select '地点（対象外を除く）',              count(*)::text from spots where status <> 'archived'
union all select '└ 収集データが全部取り残しの地点',   count(*)::text from orphan_spots
union all select '同名・同種別で近すぎる地点の組（重複の疑い）', count(*)::text from dup_pairs
union all select '└ 例', coalesce((select string_agg(name || '(' || meters || 'm)', '、')
                                     from (select * from dup_pairs order by meters limit 8) x), '—')
union all select '【確認】@番号付きの収集データ（新方式の点）', count(*)::text from raw_candidates where source_ref like '%@%'
union all select '【確認】@番号なしの収集データ',             count(*)::text from raw_candidates where source_ref not like '%@%'
union all select '【確認】旧方式の川（@なし・川）',            count(*)::text from raw_candidates where source_ref not like '%@%' and category_hint = 'river'
union all select '【確認】対象外（archived）の地点',          count(*)::text from spots where status = 'archived'
union all select '【確認】直近の収集', coalesce((select to_char(max(started_at) at time zone 'Asia/Tokyo', 'YYYY-MM-DD HH24:MI') from job_runs where job like 'collect_osm:%'), '—');
