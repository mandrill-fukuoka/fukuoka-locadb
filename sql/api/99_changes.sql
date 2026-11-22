-- 変化の材料（DB変更292）。tools/migrate.py が毎回作り直す関数
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in ('change_osm_marks_refresh','change_overture_flags')
  loop execute format('drop function %s', f.sig); end loop;
end $$;

-- OSM のタグに、廃業・解体・工事の印が新しく付いた地点を「要確認」にする。
-- feature_marks が空のとき（初回）は、今ある印を基準として覚えるだけで何も出さない（今の状態は「変化」ではないため）
create function change_osm_marks_refresh(p_limit int default 300) returns jsonb
language plpgsql as $$
declare
  v_baseline boolean;
  v_marked int; v_flagged int := 0; v_new int := 0;
begin
  v_baseline := not exists (select 1 from feature_marks);
  drop table if exists cm_now; drop table if exists cm_new;
  create temp table cm_now as
    select f.key, f.osm_type, f.osm_id,
           array_remove(array[
             case when f.tags->>'building' = 'construction' or f.tags ? 'construction' or f.tags->>'landuse' = 'construction'
                  then 'redevelopment' end,
             case when exists (select 1 from jsonb_object_keys(f.tags) k
                                where k like 'demolished%' or k like 'razed:%' or k like 'removed:%')
                  then 'demolition' end,
             case when exists (select 1 from jsonb_object_keys(f.tags) k
                                where k like 'disused:%' or k like 'abandoned:%' or k like 'was:%' or k like 'closed:%')
                    or f.tags->>'disused' = 'yes' or f.tags->>'abandoned' = 'yes' or f.tags ? 'end_date'
                  then 'closure' end
           ], null) as marks
      from features f where f.kind = 'element' and f.tags is not null;
  delete from cm_now where marks = '{}';
  select count(*) into v_marked from cm_now;

  if not v_baseline then
    create temp table cm_new as
      select n.key, n.osm_type, n.osm_id, m as mark
        from cm_now n
        left join feature_marks o on o.feature_key = n.key
        cross join lateral unnest(n.marks) m
       where o.feature_key is null or not (m = any(o.marks));
    with cand as (
      select distinct on (s.id, c.mark) s.id spot_id, s.name, c.mark, c.osm_type, c.osm_id
        from cm_new c
        join spot_features sf on sf.feature_key = c.key
        join spots s on s.id = sf.spot_id
       where s.data_status <> 'archived'
         and s.category <> all (array['bridge_overpass','underpass','expressway','railway','river','riverbank','coast',
                                      'breakwater','alley','slope_street'])
       order by s.id, c.mark
    ), ins as (
      insert into change_risks (spot_id, risk_type, evidence, source_url, fetched_at, certainty, verified, review_by, source_kind)
      select c.spot_id, c.mark,
             'OSM のタグが変わった：' || case c.mark when 'redevelopment' then '工事中の印（construction）'
                                                   when 'demolition' then '解体・撤去の印（demolished: など）'
                                                   else '廃業・閉鎖の印（disused: / abandoned: / was: / end_date など）' end
               || 'が新しく付いた（前回の取得にはなかった）',
             'https://www.openstreetmap.org/' || case c.osm_type when 'n' then 'node' when 'w' then 'way' else 'relation' end
               || '/' || c.osm_id,
             now(), 'keyword', false, current_date + 14, 'osm_tags'
        from cand c
       where not exists (select 1 from change_risks r
                          where r.spot_id = c.spot_id and r.risk_type = c.mark and r.source_kind = 'osm_tags'
                            and r.risk_type <> 'none')
       order by c.spot_id
       limit p_limit
      returning 1
    )
    select count(*) into v_flagged from ins;
    select count(*) into v_new from cm_new;
  end if;

  -- 覚え直し（今の印がすべて。印がなくなった地物は消す）
  delete from feature_marks;
  insert into feature_marks (feature_key, marks) select key, marks from cm_now;
  drop table if exists cm_now; drop table if exists cm_new;
  return jsonb_build_object('baseline', v_baseline, 'features_marked', v_marked, 'new_marks', v_new, 'flagged', v_flagged);
end $$;

-- Overture のリリース更新で消えた店の点が、地点の周り50mに多いとき「要確認」にする（place_points_gone は取り込みが作る）
create function change_overture_flags(p_limit int default 200) returns jsonb
language plpgsql as $$
declare v_flagged int := 0; v_gone int; v_rel text;
begin
  select count(*), max(release_to) into v_gone, v_rel from place_points_gone;
  if v_gone = 0 then
    return jsonb_build_object('gone', 0, 'flagged', 0);
  end if;
  with agg as (
    select s.id spot_id,
           (select count(*) from place_points_gone g where ST_DWithin(g.geom, s.geom, 50)) gone50,
           (select count(*) from place_points p where ST_DWithin(p.geom, s.geom, 50)) now50
      from spots s
     where s.data_status <> 'archived' and char_length(s.name) >= 4 and s.name not like '（無名）%'
       and s.category <> all (array['bridge_overpass','underpass','expressway','railway','river','riverbank','coast',
                                    'breakwater','alley','slope_street'])
       and exists (select 1 from place_points_gone g where ST_DWithin(g.geom, s.geom, 50))
  ), ins as (
    insert into change_risks (spot_id, risk_type, evidence, source_url, fetched_at, certainty, verified, review_by, source_kind)
    select a.spot_id, 'closure',
           'Overture の店の点：周り50mで ' || (a.gone50 + a.now50) || ' 件のうち ' || a.gone50 || ' 件が、前回のリリースから'
             || '今回（' || v_rel || '）で見えなくなった（店の入れ替わりや閉店の可能性。ID の振り直しのこともある）',
           'https://overturemaps.org/#' || v_rel, now(), 'keyword', false, current_date + 14, 'overture'
      from agg a
     where a.gone50 >= 3 and a.gone50 >= 0.6 * (a.gone50 + a.now50)
       and not exists (select 1 from change_risks r
                        where r.spot_id = a.spot_id and r.source_kind = 'overture' and r.source_url = 'https://overturemaps.org/#' || v_rel)
     order by a.gone50 desc
     limit p_limit
    returning 1
  )
  select count(*) into v_flagged from ins;
  return jsonb_build_object('gone', v_gone, 'release', v_rel, 'flagged', v_flagged);
end $$;
