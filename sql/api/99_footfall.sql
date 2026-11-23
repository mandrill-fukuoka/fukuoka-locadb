-- 駅の人の多さ（DB変更293）。tools/migrate.py が毎回作り直す
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in ('footfall_refresh','footfall_stats')
  loop execute format('drop function %s', f.sig); end loop;
end $$;

-- 地点ごとの駅の人の多さを数え直す。station_flow が空なら何もしない（＝vibe は店の数だけで見る）。戻り値は更新した地点数
create function footfall_refresh() returns integer language plpgsql as $$
declare v_n int;
begin
  if not exists (select 1 from station_flow limit 1) then return 0; end if;
  insert into spot_footfall (spot_id, flow_800, station_m, station_name, station_pax, updated_at)
  select s.id,
         coalesce(round(sum(f.pax * greatest(0, 1 - ST_Distance(f.geom, s.geom) / 800.0))), 0)::int,
         (min(ST_Distance(f.geom, s.geom)))::int,
         (array_agg(f.name order by ST_Distance(f.geom, s.geom)))[1],
         (array_agg(f.pax order by ST_Distance(f.geom, s.geom)))[1],
         now()
    from spots s left join station_flow f on ST_DWithin(f.geom, s.geom, 800)
   where s.data_status <> 'archived'
   group by s.id
  on conflict (spot_id) do update set flow_800 = excluded.flow_800, station_m = excluded.station_m,
         station_name = excluded.station_name, station_pax = excluded.station_pax, updated_at = excluded.updated_at;
  get diagnostics v_n = row_count;
  delete from spot_footfall d using spots s where s.id = d.spot_id and s.data_status = 'archived';
  return v_n;
end $$;

-- 分布（読み取りのみ）：取り込みの結果の確認用
create function footfall_stats() returns jsonb language sql stable as $$
select jsonb_build_object(
  'stations', (select count(*) from station_flow),
  'fiscal_year', (select max(fiscal_year) from station_flow),
  'spots', (select count(*) from spot_footfall),
  'spots_with_station_800', (select count(*) from spot_footfall where station_m is not null),
  'flow_800_p10_50_90_99', (select to_jsonb(percentile_disc(array[.1,.5,.9,.99]) within group (order by flow_800)) from spot_footfall)
);
$$;
