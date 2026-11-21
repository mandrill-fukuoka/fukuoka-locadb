-- サイトマップ用（追補1。DB変更なし。tools/migrate.py が毎回作り直す）
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in ('sitemap_count','sitemap_spots')
  loop execute format('drop function %s', f.sig); end loop;
end $$;

-- 載せてよい地点の数（条件は spot_is_indexable と同じ）
create function sitemap_count() returns bigint language sql stable as $$
  select count(*) from spots s
   where s.data_status = 'current' and s.name not like '（無名）%'
     and (s.summary is not null or s.ffc_registered
          or exists (select 1 from spot_facts f where f.spot_id = s.id and f.status = 'approved')
          or exists (select 1 from spot_photos ph where ph.spot_id = s.id));
$$;

-- 載せてよい地点の id と更新日（id 順。ページ送りは offset・limit）
create function sitemap_spots(p_offset int default 0, p_limit int default 10000) returns jsonb language sql stable as $$
select coalesce(jsonb_agg(jsonb_build_object('id', x.id, 'updated_at', x.updated_at) order by x.id), '[]'::jsonb) from (
  select s.id, s.updated_at from spots s
   where s.data_status = 'current' and s.name not like '（無名）%'
     and (s.summary is not null or s.ffc_registered
          or exists (select 1 from spot_facts f where f.spot_id = s.id and f.status = 'approved')
          or exists (select 1 from spot_photos ph where ph.spot_id = s.id))
   order by s.id offset greatest(p_offset, 0) limit greatest(least(p_limit, 50000), 1)
) x;
$$;
