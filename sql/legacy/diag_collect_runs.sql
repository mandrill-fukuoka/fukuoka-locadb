-- ============================================================
-- 診断（読み取りのみ）：区ごとに、直近2回の収集で種別ごとの取得件数を比べる
-- 件数が急に減った種別は、OSMの変化ではなく「途中で切れた応答」の可能性が高い
-- ============================================================
with runs as (
  select split_part(job, ':', 2) as area, started_at, stats,
         row_number() over (partition by split_part(job, ':', 2) order by started_at desc) as n
    from job_runs
   where job like 'collect_osm:%' and finished_at is not null and error is null
),
cnt as (
  select r.area, r.n, r.started_at, k.key as cat, (k.value)::text::int as got
    from runs r, jsonb_each(r.stats) k
   where r.n <= 2 and jsonb_typeof(k.value) = 'number'
)
select a.area as 区, sc.name as 種別,
       b.got as 前回, a.got as 直近, a.got - b.got as 差,
       to_char(b.started_at at time zone 'Asia/Tokyo', 'MM-DD HH24:MI') as 前回日時,
       to_char(a.started_at at time zone 'Asia/Tokyo', 'MM-DD HH24:MI') as 直近日時
  from cnt a
  join cnt b on b.area = a.area and b.cat = a.cat and b.n = 2
  left join spot_categories sc on sc.code = a.cat
 where a.n = 1 and a.got <> b.got
 order by (a.got - b.got), a.area;
