-- ============================================================
-- 005: その場でルートを組み立てる検索・おすすめの流れ・ピンポイント検索
-- Supabase の SQL Editor に全文を貼り付けて Run（何度実行しても安全）
-- ============================================================

-- ---------- クラスター表の拡張（検索から生まれた流れを保存） ----------
alter table clusters drop constraint if exists clusters_origin_check;
alter table clusters add constraint clusters_origin_check check (origin in ('auto','human','search'));
alter table clusters add column if not exists route_key  text unique;      -- 地点IDを - でつないだもの
alter table clusters add column if not exists hit_count  int not null default 0;  -- 検索結果に出た回数
alter table clusters add column if not exists last_hit_at timestamptz;
alter table clusters add column if not exists saved_by   text;             -- 「おすすめに保存」した人
alter table clusters add column if not exists note       text;

-- ---------- 検索ログ ----------
create table if not exists search_logs (
  id             bigserial primary key,
  kind           text not null default 'route',   -- route / place
  scene          text,
  seq            text[],
  tags           jsonb,
  travel_mode    text,
  area_code      text,
  start_spot     bigint,
  result_count   int,
  top_route_keys text[],
  searched_by    text,
  created_at     timestamptz not null default now()
);
create index if not exists search_logs_created_idx on search_logs (created_at desc);

-- ---------- ルートの組み立て ----------
-- p_seq   : 景観グループの順番（例 {commercial,old_town,water}）
-- p_tags  : 雰囲気タグの重み（例 {"lonely":1,"night_ok":0.8}）
-- p_mode  : walk / car / any
-- p_area  : 起点のエリア（null ならすべて）
-- p_start : 起点にする地点ID（ピンポイント検索から「ここから流れを探す」）
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
    if cardinality(v_seq) > 0 and v_seq[1] = v_sgrp then v_seq := v_seq[2:]; end if;
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
                        else sc.landscape_group = v_seq[1] and (p_area is null or s.area_code = p_area) end
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
               and sc.landscape_group = v_seq[p.step + 1]
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

-- ---------- ルートを「流れ」として保存（検索ログからの自動保存・手動保存の両方） ----------
create or replace function upsert_route(p_route jsonb, p_origin text default 'search', p_user text default null)
returns bigint language plpgsql volatile as $$
declare
  v_key text := p_route ->> 'route_key';
  v_id  bigint;
  v_ids bigint[];
  i int;
begin
  if v_key is null or v_key = '' then return null; end if;
  select id into v_id from clusters where route_key = v_key;
  if v_id is null then
    v_ids := string_to_array(v_key, '-')::bigint[];
    insert into clusters (code, name, area_code, transition_score, transition_label, total_minutes,
                          travel_mode, origin, route_key, hit_count, last_hit_at)
    values ('FUK-R' || substr(md5(v_key), 1, 8),
            (select name from spots where id = v_ids[1]) || ' 〜 ' || (select name from spots where id = v_ids[cardinality(v_ids)]),
            (select area_code from spots where id = v_ids[1]),
            (p_route ->> 'match')::real, p_route ->> 'transition_label',
            (p_route ->> 'total_minutes')::real, p_route ->> 'travel_mode', p_origin, v_key, 0, now())
    returning id into v_id;
    for i in 1 .. cardinality(v_ids) loop
      insert into cluster_spots (cluster_id, seq, spot_id, minutes_from_prev)
      values (v_id, i - 1, v_ids[i], (p_route -> 'spots' -> (i - 1) ->> 'minutes_from_prev')::real);
    end loop;
  end if;
  if p_user is not null then
    update clusters set status = 'reviewed', saved_by = p_user where id = v_id;
  end if;
  return v_id;
end $$;

-- 検索1回分を記録し、上位3ルートを「流れ」として自動保存（出現回数を数える）
create or replace function record_route_search(
  p_scene text, p_seq text[], p_tags jsonb, p_mode text, p_area text, p_start bigint,
  p_routes jsonb, p_user text
) returns void language plpgsql volatile as $$
declare
  r jsonb; v_id bigint; v_keys text[] := '{}'; n int := 0;
begin
  for r in select * from jsonb_array_elements(coalesce(p_routes, '[]'::jsonb)) loop
    n := n + 1;
    exit when n > 3;
    v_id := upsert_route(r, 'search', null);
    update clusters set hit_count = hit_count + 1, last_hit_at = now() where id = v_id;
    v_keys := v_keys || (r ->> 'route_key');
  end loop;
  insert into search_logs (kind, scene, seq, tags, travel_mode, area_code, start_spot, result_count, top_route_keys, searched_by)
  values ('route', p_scene, p_seq, p_tags, p_mode, p_area, p_start,
          jsonb_array_length(coalesce(p_routes, '[]'::jsonb)), v_keys, p_user);
end $$;

-- ---------- おすすめの流れ ----------
create or replace function recommended_flows(p_area text default null, p_filter text default 'all', p_limit int default 30)
returns jsonb language sql stable as $$
select coalesce(jsonb_agg(x), '[]'::jsonb) from (
  select c.id, c.code, c.name, c.transition_label, c.total_minutes, c.travel_mode, c.area_code,
         c.status, c.origin, c.hit_count, c.last_hit_at, c.saved_by, c.route_key,
         round(coalesce(c.transition_score, 0)::numeric, 3) as match,
         (select jsonb_agg(jsonb_build_object(
                   'id', s.id, 'code', s.code, 'name', s.name, 'group', lg.name,
                   'lat', ST_Y(s.geom::geometry), 'lon', ST_X(s.geom::geometry),
                   'minutes_from_prev', cs.minutes_from_prev, 'confidence', s.confidence, 'summary', s.summary,
                   'risk', exists (select 1 from change_risks rk where rk.spot_id = s.id and rk.risk_type <> 'none'))
                 order by cs.seq)
            from cluster_spots cs join spots s on s.id = cs.spot_id
            join spot_categories sc on sc.code = s.category
            join landscape_groups lg on lg.code = sc.landscape_group
           where cs.cluster_id = c.id) as spots
    from clusters c
   where c.status <> 'archived'
     and (p_area is null or c.area_code = p_area)
     and case p_filter
           when 'saved'   then c.status in ('reviewed','verified')
           when 'popular' then c.origin = 'search'
           when 'auto'    then c.origin = 'auto'
           else c.origin in ('search','human') or c.status in ('reviewed','verified')
         end
   order by (c.status in ('reviewed','verified')) desc, c.hit_count desc, c.last_hit_at desc nulls last, c.transition_score desc nulls last
   limit p_limit
) x;
$$;

-- よく探される景観の流れ（検索ログの集計）
create or replace function popular_sequences(p_limit int default 10)
returns jsonb language sql stable as $$
select coalesce(jsonb_agg(x), '[]'::jsonb) from (
  select seq, count(*) as times, max(created_at) as last_at
    from search_logs
   where kind = 'route' and cardinality(seq) > 0
   group by seq
   order by count(*) desc, max(created_at) desc
   limit p_limit
) x;
$$;

-- 流れを外す（おすすめから削除）
create or replace function archive_flow(p_id bigint) returns void language sql volatile as $$
  update clusters set status = 'archived' where id = p_id and origin <> 'auto';
$$;

-- ---------- ピンポイント検索（場所・施設） ----------
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
     and (p_group is null or sc.landscape_group = p_group)
     and (p_tags is null or cardinality(p_tags) = 0 or exists (
            select 1 from spot_tags st where st.spot_id = s.id and st.tag_code = any (p_tags)))
   order by (s.name like '（無名）%'),
            case when p_q is null then 0 else similarity(s.name, p_q) end desc,
            (s.confidence in ('desk_checked','field_checked')) desc,
            s.code
   offset p_offset limit p_limit
) x;
$$;

-- 地点の周辺（半径m以内）を景観グループ別に
create or replace function spots_nearby(p_id bigint, p_radius real default 800)
returns jsonb language sql stable as $$
select coalesce(jsonb_agg(x order by x.meters), '[]'::jsonb) from (
  select n.id, n.name, lg.name as grp, round(ST_Distance(n.geom, s.geom)) as meters,
         ST_Y(n.geom::geometry) as lat, ST_X(n.geom::geometry) as lon
    from spots s
    join spots n on n.id <> s.id and ST_DWithin(n.geom, s.geom, p_radius) and n.status <> 'archived'
    join spot_categories sc on sc.code = n.category
    join landscape_groups lg on lg.code = sc.landscape_group
   where s.id = p_id and n.name not like '（無名）%'
   order by ST_Distance(n.geom, s.geom)
   limit 30
) x;
$$;

-- ---------- 権限（サーバー側の service_role のみ） ----------
do $$
declare f text;
begin
  foreach f in array array[
    'build_routes(text[],jsonb,text,text,bigint,integer)',
    'upsert_route(jsonb,text,text)',
    'record_route_search(text,text[],jsonb,text,text,bigint,jsonb,text)',
    'recommended_flows(text,text,integer)',
    'popular_sequences(integer)',
    'archive_flow(bigint)',
    'search_spots(text,text,text,text,text[],integer,integer)',
    'spots_nearby(bigint,real)'
  ] loop
    execute format('revoke execute on function %s from public', f);
    if exists (select 1 from pg_roles where rolname = 'anon') then
      execute format('revoke execute on function %s from anon', f);
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
      execute format('revoke execute on function %s from authenticated', f);
    end if;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
      execute format('grant execute on function %s to service_role', f);
    end if;
  end loop;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant select, insert, update, delete on search_logs to service_role;
    grant usage, select on all sequences in schema public to service_role;
  end if;
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on search_logs from anon;
  end if;
end $$;
