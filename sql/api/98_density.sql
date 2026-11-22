-- 店の密度（順6の後半、DB変更290。tools/migrate.py が毎回作り直す）
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in ('density_refresh','density_stats')
  loop execute format('drop function %s', f.sig); end loop;
end $$;

-- 地点ごとの周りの数を作り直す。place_points が空なら何もしない（＝vibe は地点の数で数える従来どおり）。戻り値は更新した地点数
create function density_refresh() returns integer language plpgsql as $$
declare v_n int;
begin
  if not exists (select 1 from place_points limit 1) then return 0; end if;
  insert into spot_density (spot_id, bar_150, eat_150, shop_150, any_150, any_500, updated_at)
  select s.id,
         count(p.id) filter (where p.cls = 'bar'    and ST_DWithin(p.geom, s.geom, 150)),
         count(p.id) filter (where p.cls = 'eat'    and ST_DWithin(p.geom, s.geom, 150)),
         count(p.id) filter (where p.cls in ('bar','eat','retail') and ST_DWithin(p.geom, s.geom, 150)),
         count(p.id) filter (where ST_DWithin(p.geom, s.geom, 150)),
         count(p.id),
         now()
    from spots s left join place_points p on ST_DWithin(p.geom, s.geom, 500)
   where s.data_status <> 'archived'
   group by s.id
  on conflict (spot_id) do update set bar_150 = excluded.bar_150, eat_150 = excluded.eat_150, shop_150 = excluded.shop_150,
         any_150 = excluded.any_150, any_500 = excluded.any_500, updated_at = excluded.updated_at;
  get diagnostics v_n = row_count;
  delete from spot_density d using spots s where s.id = d.spot_id and s.data_status = 'archived';
  return v_n;
end $$;

-- 分布（読み取りのみ）：取り込みの結果の確認用
create function density_stats() returns jsonb language sql stable as $$
select jsonb_build_object(
  'points', (select count(*) from place_points),
  'by_cls', (select coalesce(jsonb_object_agg(cls, n), '{}'::jsonb) from (select cls, count(*) n from place_points group by cls) t),
  'release', (select max(release) from place_points),
  'spots', (select count(*) from spot_density),
  'shop_150_p10_50_90_99', (select to_jsonb(percentile_disc(array[.1,.5,.9,.99]) within group (order by shop_150)) from spot_density),
  'bar_150_p10_50_90_99', (select to_jsonb(percentile_disc(array[.1,.5,.9,.99]) within group (order by bar_150)) from spot_density),
  'any_500_p10_50_90_99', (select to_jsonb(percentile_disc(array[.1,.5,.9,.99]) within group (order by any_500)) from spot_density));
$$;
