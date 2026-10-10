-- ============================================================
-- 診断の内訳（読み取りのみ）：種別ごとに「取り残し」「全部取り残しの地点」「重複の疑い」を数える
-- ============================================================
with last_job as (
  select distinct on (split_part(job, ':', 2))
         split_part(job, ':', 2) as area, started_at, coalesce(stats->'skipped', '[]'::jsonb) as skipped
    from job_runs
   where job like 'collect_osm:%' and finished_at is not null and error is null
   order by split_part(job, ':', 2), started_at desc
),
stale_raw as (
  select rc.* from raw_candidates rc join last_job j on j.area = rc.raw->>'area'
   where rc.source = 'osm' and rc.fetched_at < j.started_at and not (j.skipped ? rc.category_hint)
),
orphan as (
  select s.id, s.category from spots s
   where s.status <> 'archived'
     and exists (select 1 from raw_candidates rc where rc.spot_id = s.id)
     and not exists (select 1 from raw_candidates rc where rc.spot_id = s.id and rc.id not in (select id from stale_raw))
),
dup as (
  select a.category from spots a
    join spots b on b.id > a.id and b.name = a.name and b.category = a.category and ST_DWithin(a.geom, b.geom, 300)
   where a.status <> 'archived' and b.status <> 'archived' and a.name not like '（無名）%'
     and ( a.category not in ('river','coast','railway','shotengai','public_space')
           or ST_Distance(a.geom, b.geom) < case a.category when 'railway' then 300
                                                            when 'river' then 200 when 'coast' then 200 else 75 end )
),
cats as (select code, name from spot_categories)
select c.name as 種別,
       (select count(*) from raw_candidates r where r.category_hint = c.code)          as 収集データ,
       (select count(*) from stale_raw r where r.category_hint = c.code)               as 取り残し,
       (select count(*) from spots s where s.category = c.code and s.status <> 'archived') as 地点,
       (select count(*) from orphan o where o.category = c.code)                       as 全部取り残しの地点,
       (select count(*) from dup d where d.category = c.code)                          as 重複の疑い
  from cats c
 order by 3 desc, 6 desc;
