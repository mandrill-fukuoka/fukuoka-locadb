-- 昼の人の多さ（DB変更294）。tools/migrate.py が毎回作り直す
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in ('daytime_refresh','daytime_stats')
  loop execute format('drop function %s', f.sig); end loop;
end $$;

-- 地点ごとの周り800mの従業者数を数え直す。workplace_mesh が空なら何もしない（＝vibe は今までどおり）。戻り値は更新した地点数
create function daytime_refresh() returns integer language plpgsql as $$
declare v_n int;
begin
  if not exists (select 1 from workplace_mesh limit 1) then return 0; end if;
  insert into spot_daytime (spot_id, workers_800, updated_at)
  select s.id,
         coalesce(round(sum(m.workers * greatest(0, 1 - ST_Distance(m.geom, s.geom) / 800.0))), 0)::int,
         now()
    from spots s left join workplace_mesh m on ST_DWithin(m.geom, s.geom, 800)
   where s.data_status <> 'archived'
   group by s.id
  on conflict (spot_id) do update set workers_800 = excluded.workers_800, updated_at = excluded.updated_at;
  get diagnostics v_n = row_count;
  delete from spot_daytime d using spots s where s.id = d.spot_id and s.data_status = 'archived';
  return v_n;
end $$;

-- 分布（読み取りのみ）：取り込みの結果の確認用
create function daytime_stats() returns jsonb language sql stable as $$
select jsonb_build_object(
  'meshes', (select count(*) from workplace_mesh),
  'survey_year', (select max(survey_year) from workplace_mesh),
  'workers_total', (select sum(workers) from workplace_mesh),
  'spots', (select count(*) from spot_daytime),
  'spots_with_workers_800', (select count(*) from spot_daytime where workers_800 > 0),
  'workers_800_p10_50_90_99', (select to_jsonb(percentile_disc(array[.1,.5,.9,.99]) within group (order by workers_800)) from spot_daytime)
);
$$;
