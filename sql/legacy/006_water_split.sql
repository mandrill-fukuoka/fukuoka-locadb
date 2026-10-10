-- ============================================================
-- 006: 「河川・海・水辺」を 川 / 海 / 砂浜 に分ける
-- Supabase の SQL Editor に全文を貼り付けて Run（何度実行しても安全）
-- ============================================================

-- 景観グループ
insert into landscape_groups (code, name) values
  ('river', '川・河川'),
  ('sea',   '海・海辺'),
  ('beach', '砂浜')
on conflict (code) do update set name = excluded.name;
-- 旧「河川・海・水辺」は、川・海・砂浜のどれでもよい「まとめ」の選択肢として残す
update landscape_groups set name = '水辺（川・海・砂浜のどれでも）' where code = 'water';

-- 地点の種別
insert into spot_categories (code, name, landscape_group) values ('beach', '砂浜・海水浴場', 'beach')
on conflict (code) do update set name = excluded.name, landscape_group = excluded.landscape_group;
update spot_categories set name = '河川・河川敷・運河', landscape_group = 'river' where code = 'river';
update spot_categories set name = '海岸・護岸・干潟',   landscape_group = 'sea'   where code = 'coast';
update spot_categories set landscape_group = 'sea' where code in ('fishing_port', 'island');

-- 登録済みの「海岸」のうち、地図データで砂浜のものを「砂浜」へ
update spots set category = 'beach'
 where category = 'coast'
   and id in (select spot_id from raw_candidates
               where spot_id is not null
                 and (raw -> 'tags' ->> 'natural' = 'beach' or raw -> 'tags' ->> 'leisure' = 'beach_resort'));
-- 名前でも補正（〜浜 / ビーチ / 海水浴場）
update spots set category = 'beach'
 where category = 'coast' and name ~ '(浜|ビーチ|海水浴場)$';

-- 景観の一致判定（water は 川・海・砂浜 のどれでも一致）
create or replace function grp_match(p_want text, p_actual text) returns boolean
language sql immutable as $$
  select p_want = p_actual or (p_want = 'water' and p_actual in ('river', 'sea', 'beach', 'water'));
$$;

-- ルートの組み立て・場所検索を更新（まとめの「水辺」に対応）
create or replace function build_routes(
  p_seq text[], p_tags jsonb default '{}'::jsonb, p_mode text default 'walk',
  p_area text default null, p_start bigint default null, p_limit int default 8
) returns jsonb language plpgsql volatile as $$
declare
  v_speed  real := case when p_mode = 'car' then 300 else 80 end;   -- m/分
  v_budget real := 15;                                              -- 合計の上限（分）
  v_maxm   real;
  v_seq    text[] := coalesce(p_seq, '{}');
  v_tags   jsonb  := coalesce(p_tags, '{}'::jsonb);
  v_tagw   real;
  v_sgrp   text;
  v_n      int;
  v_out    jsonb;
begin
  if p_mode = 'any' then
    return coalesce(build_routes(p_seq, p_tags, 'walk', p_area, p_start, greatest(p_limit - p_limit / 2, 1)), '[]'::jsonb)
        || coalesce(build_routes(p_seq, p_tags, 'car',  p_area, p_start, greatest(p_limit / 2, 1)), '[]'::jsonb);
  end if;

  v_maxm := v_budget * v_speed / 1.3;    -- 直線距離 × 迂回係数1.3 で概算
  select greatest(coalesce(sum(value::real), 0), 0.0001) into v_tagw from jsonb_each_text(v_tags);

  if p_start is not null then
    select sc.landscape_group into v_sgrp
      from spots s join spot_categories sc on sc.code = s.category where s.id = p_start;
    if cardinality(v_seq) > 0 and grp_match(v_seq[1], v_sgrp) then v_seq := v_seq[2:]; end if;
    v_seq := array_prepend(coalesce(v_sgrp, '_start'), v_seq);
  end if;
  v_n := cardinality(v_seq);
  if v_n = 0 then return '[]'::jsonb; end if;

  with recursive paths as (
    -- 起点
    select 1 as step, array[c.id] as ids, c.geom as last_geom, 0::real as meters,
           c.tag as tagsum, (case when c.unnamed then 1 else 0 end) as unnamed_n,
           (case when c.checked then 1 else 0 end) as checked_n
      from (select s.id, s.geom,
                   (s.name like '（無名）%') as unnamed,
                   (s.confidence in ('desk_checked','field_checked')) as checked,
                   coalesce(case when v_tagw < 0.001 then 0 else
                     (select sum((v_tags ->> t.tag_code)::real * t.score)
                        from spot_tags t where t.spot_id = s.id and v_tags ? t.tag_code) / v_tagw end, 0) as tag
              from spots s join spot_categories sc on sc.code = s.category
             where s.status <> 'archived'
               and case when p_start is not null then s.id = p_start
                        else grp_match(v_seq[1], sc.landscape_group) and (p_area is null or s.area_code = p_area) end
             order by tag desc, checked desc, unnamed, random()
             limit 150) c
    union all
    -- 次の景観へ（近くて雰囲気の合う地点を最大3件ずつ。空間インデックスで近傍だけを探す）
    select p.step + 1, p.ids || n.id, n.geom, p.meters + n.d, p.tagsum + n.tag,
           p.unnamed_n + (case when n.unnamed then 1 else 0 end),
           p.checked_n + (case when n.checked then 1 else 0 end)
      from paths p
      cross join lateral (
        select k.*, coalesce(case when v_tagw < 0.001 then 0 else
                 (select sum((v_tags ->> t.tag_code)::real * t.score)
                    from spot_tags t where t.spot_id = k.id and v_tags ? t.tag_code) / v_tagw end, 0) as tag
          from (
            -- 近い順に30件まで（空間インデックスで高速に絞る）
            select s.id, s.geom,
                   (s.name like '（無名）%') as unnamed,
                   (s.confidence in ('desk_checked','field_checked')) as checked,
                   ST_Distance(s.geom, p.last_geom)::real as d
              from spots s join spot_categories sc on sc.code = s.category
             where p.step < v_n
               and ST_DWithin(s.geom, p.last_geom, v_maxm - p.meters)
               and grp_match(v_seq[p.step + 1], sc.landscape_group)
               and s.status <> 'archived'
               and not (s.id = any (p.ids))
               and ST_Distance(s.geom, p.last_geom) > 30
             order by s.geom <-> p.last_geom
             limit 30
          ) k
         order by tag desc, k.checked desc, k.unnamed, k.d
         limit 3
      ) n
  ),
  done as (
    select ids, meters,
           round((meters * 1.3 / v_speed)::numeric, 1) as minutes,
           ( 0.45 * least(tagsum / v_n, 1)
           + 0.30 * (1 - least(meters * 1.3 / v_speed / v_budget, 1))
           + 0.15 * (1 - unnamed_n::real / v_n)
           + 0.10 * (checked_n::real / v_n) ) as score
      from paths
     where step = v_n
  ),
  picked as (       -- 似た候補ばかりにならないよう、起点・終点ごとに最良の1件
    select * from (
      select d.*, row_number() over (partition by ids[cardinality(ids)] order by score desc) as rn2
        from (select d.*, row_number() over (partition by ids[1] order by score desc) as rn1 from done d) d
       where rn1 = 1
    ) x where rn2 = 1
    order by score desc
    limit p_limit
  )
  select coalesce(jsonb_agg(q.r order by q.score desc), '[]'::jsonb) into v_out
    from (
      select pk.score,
             jsonb_build_object(
               'route_key', array_to_string(pk.ids, '-'),
               'travel_mode', p_mode,
               'total_minutes', pk.minutes,
               'match', round(pk.score::numeric, 3),
               'area_code', (select area_code from spots where id = pk.ids[1]),
               'transition_label', (select string_agg(lg.name, ' → ' order by u.ord)
                                      from unnest(pk.ids) with ordinality u(id, ord)
                                      join spots s on s.id = u.id
                                      join spot_categories sc on sc.code = s.category
                                      join landscape_groups lg on lg.code = sc.landscape_group),
               'spots', (select jsonb_agg(jsonb_build_object(
                             'id', w.id, 'code', w.code, 'name', w.name, 'group', w.grp,
                             'lat', ST_Y(w.geom::geometry), 'lon', ST_X(w.geom::geometry),
                             'minutes_from_prev', case when w.prev_geom is null then null else
                                round((ST_Distance(w.geom, w.prev_geom) * 1.3 / v_speed)::numeric, 1) end,
                             'confidence', w.confidence, 'summary', w.summary,
                             'risk', exists (select 1 from change_risks rk where rk.spot_id = w.id and rk.risk_type <> 'none'))
                           order by w.ord)
                           from (select s.id, s.code, s.name, s.geom, s.confidence, s.summary, lg.name as grp, u.ord,
                                        lag(s.geom) over (order by u.ord) as prev_geom
                                   from unnest(pk.ids) with ordinality u(id, ord)
                                   join spots s on s.id = u.id
                                   join spot_categories sc on sc.code = s.category
                                   join landscape_groups lg on lg.code = sc.landscape_group) w)
             ) as r
        from picked pk
    ) q;
  return coalesce(v_out, '[]'::jsonb);
end $$;

create or replace function search_spots(
  p_q text default null, p_area text default null, p_category text default null,
  p_group text default null, p_tags text[] default null, p_offset int default 0, p_limit int default 40
) returns jsonb language sql stable as $$
select coalesce(jsonb_agg(x), '[]'::jsonb) from (
  select s.id, s.code, s.name, s.area_code, s.address, sc.name as category, lg.name as grp,
         s.era_feel, s.shoot_scope, s.status, s.confidence, s.summary, s.ffc_registered,
         ST_Y(s.geom::geometry) as lat, ST_X(s.geom::geometry) as lon,
         (select jsonb_agg(t.name order by st.score desc) from spot_tags st join tags t on t.code = st.tag_code
           where st.spot_id = s.id and st.score >= 0.5) as tags,
         exists (select 1 from change_risks rk where rk.spot_id = s.id and rk.risk_type <> 'none') as risk
    from spots s
    join spot_categories sc on sc.code = s.category
    join landscape_groups lg on lg.code = sc.landscape_group
   where s.status <> 'archived'
     and (p_q is null or s.name ilike '%' || p_q || '%' or s.address ilike '%' || p_q || '%'
          or array_to_string(s.alt_names, ' ') ilike '%' || p_q || '%' or similarity(s.name, p_q) > 0.3)
     and (p_area is null or s.area_code = p_area)
     and (p_category is null or s.category = p_category)
     and (p_group is null or grp_match(p_group, sc.landscape_group))
     and (p_tags is null or cardinality(p_tags) = 0 or exists (
            select 1 from spot_tags st where st.spot_id = s.id and st.tag_code = any (p_tags)))
   order by (s.name like '（無名）%'),
            case when p_q is null then 0 else similarity(s.name, p_q) end desc,
            (s.confidence in ('desk_checked','field_checked')) desc,
            s.code
   offset p_offset limit p_limit
) x;
$$;

-- 権限
do $$
declare f text;
begin
  foreach f in array array[
    'grp_match(text,text)',
    'build_routes(text[],jsonb,text,text,bigint,integer)',
    'search_spots(text,text,text,text,text[],integer,integer)'
  ] loop
    execute format('revoke execute on function %s from public', f);
    if exists (select 1 from pg_roles where rolname = 'anon') then execute format('revoke execute on function %s from anon', f); end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then execute format('revoke execute on function %s from authenticated', f); end if;
    if exists (select 1 from pg_roles where rolname = 'service_role') then execute format('grant execute on function %s to service_role', f); end if;
  end loop;
end $$;

-- 結果確認（川・海・砂浜の件数）
select lg.name as 景観, count(s.id) as 地点数
  from landscape_groups lg
  left join spot_categories sc on sc.landscape_group = lg.code
  left join spots s on s.category = sc.code and s.status <> 'archived'
 where lg.code in ('river', 'sea', 'beach', 'port_industrial')
 group by lg.code, lg.name order by lg.code;
