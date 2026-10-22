-- 画面（PHP）から呼ぶ関数：地点まわり。tools/migrate.py が毎回作り直す（何度実行しても同じ結果）
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in (
              'grp_match','search_spots','spots_nearby','spot_detail','review_queue','review_spot',
              'photo_state','spatial_view','sp_open','sp_built','sp_water','sp_road','set_spot_state','review_risk','ping','diag_summary','duplicate_candidates','merge_spots','spots_by_ids')
  loop execute format('drop function %s', f.sig); end loop;
end $$;

-- 景観の一致（water は 川・海・砂浜・池 のどれでも）
create function grp_match(p_want text, p_actual text) returns boolean language sql immutable as $$
  select p_want = p_actual or (p_want = 'water' and p_actual in ('river','sea','beach','lake','water'));
$$;

-- 周辺の特徴の段階（空間特徴 spot_spatial。仕様書10章タスクI。2026-10-18 利用者と合意・暫定）
--  境界は sp_* の4関数だけに書く（画面の表示 spatial_view と絞り込み search_spots が同じものを使う）。
--  real は numeric にしてから境界と比べる＝0.7 ちょうどが 0.69999 にならない。見通し(sight_max_m)は段階にしない。
create function sp_open(v real) returns text language sql immutable as $$
  select case when v is null then null when v::numeric >= 0.7 then 'open' when v::numeric >= 0.35 then 'mid' else 'closed' end;
$$;
create function sp_built(v real) returns text language sql immutable as $$
  select case when v is null then null when v::numeric < 0.15 then 'few' when v::numeric < 0.30 then 'mid' else 'many' end;
$$;
create function sp_water(d real) returns text language sql immutable as $$     -- 水辺なし（null）も 'none'
  select case when d is null or d::numeric >= 150 then 'none' when d::numeric < 50 then 'near' else 'close' end;
$$;
create function sp_road(cls text, w real) returns text language sql immutable as $$
  select case when cls is null or w is null then null when w::numeric < 5 then 'narrow'
              when w::numeric >= 6.5 then 'wide' else 'normal' end;
$$;

-- 写真の状態（ログイン時だけ画面に出す。候補は未公開なので公開画面には出さない）
--  has＝採用済み（公開中） / cand＝未決の候補あり / none＝探索済みで候補なし（却下だけも含む） / todo＝対象種別だがまだ探していない / na＝探索の対象外（無名・対象外の種別）
create function photo_state(p_id bigint, p_cat text, p_unnamed boolean default false) returns text language sql stable as $$
select case
  when exists (select 1 from spot_photos where spot_id = p_id) then 'has'
  when exists (select 1 from spot_photo_candidates where spot_id = p_id and status = 'candidate') then 'cand'
  when exists (select 1 from spot_photo_search where spot_id = p_id)
    or exists (select 1 from spot_photo_search_mly where spot_id = p_id) then 'none'
  when not coalesce(p_unnamed, false) and p_cat = any (array['shrine_temple','heritage','park','station','bridge_overpass','school','civic','hill',
         'island','beach','fishing_port','port','pond','commercial_facility','new_building']) then 'todo'
  else 'na' end;
$$;

-- 周辺の特徴（公開画面に出す言葉）。confidence=low（周囲300m以内の建物が3棟未満）は段階を出さず status='low'（画面は「未判定」）。
--  計算していなければ null
create function spatial_view(p_id bigint) returns jsonb language sql stable as $$
select case when ss.confidence = 'low' then jsonb_build_object('status', 'low')
  else jsonb_build_object('status', 'ok',
    'open',  case sp_open(ss.open_ratio) when 'open' then '開けている' when 'mid' then 'ふつう' else '囲まれている' end,
    'built', case sp_built(ss.building_cover_100) when 'few' then '少ない' when 'mid' then '中くらい' else '多い' end,
    'water', case sp_water(ss.water_dist_m) when 'near' then 'すぐそば' when 'close' then '近い' else 'なし' end,
    'water_kind', case when sp_water(ss.water_dist_m) <> 'none' then ss.water_kind end,
    'road',  case sp_road(ss.road_class, ss.road_width_m) when 'narrow' then '狭い道' when 'wide' then '広い道'
                  when 'normal' then 'ふつうの道' end)
  end
  from spot_spatial ss where ss.spot_id = p_id;
$$;

-- 場所を探す。川・線路などの「線」の地点は、同じ線ごとに1件にまとめて「ほか○か所」を返す
-- （名前が同じでも別の施設＝点の地点はまとめない）
create function search_spots(
  p_q text default null, p_area text default null, p_category text default null,
  p_group text default null, p_tags text[] default null, p_offset int default 0, p_limit int default 40,
  p_relief_min real default null,
  p_open text default null, p_water text default null, p_road text default null,
  p_built text default null, p_photo text default null
) returns jsonb language sql stable as $$
with hits as (
  select s.id, s.code, s.name, s.area_code, s.address, s.category as cat, sc.name as category,
         sc.landscape_group as grp_code, lg.name as grp, s.era_feel, s.shoot_scope, s.status, s.confidence,
         s.data_status, s.summary, s.ffc_registered, s.geom, s.line_key, s.position_kind,
         s.relief_m, s.slope_grade_pct, s.slope_rise_m,
         (s.name like '（無名）%') as unnamed,
         (select count(*) from spot_facts f where f.spot_id = s.id and f.status = 'approved') as fact_count
    from spots s
    join spot_categories sc on sc.code = s.category
    join landscape_groups lg on lg.code = sc.landscape_group
   where s.data_status <> 'archived'
     and (p_q is null or s.name ilike '%' || p_q || '%' or s.address ilike '%' || p_q || '%'
          or array_to_string(s.alt_names, ' ') ilike '%' || p_q || '%' or similarity(s.name, p_q) > 0.3)
     and (p_area is null or s.area_code = p_area)
     and (p_category is null or s.category = p_category or p_category = any (s.extra_categories))
     and (p_group is null or grp_match(p_group, sc.landscape_group))
     and (p_relief_min is null or s.relief_m >= p_relief_min)      -- 高台（周りより p_relief_min m 以上高い）
     and (p_open is null and p_water is null and p_road is null and p_built is null or exists (      -- 周辺の特徴（推定）。信頼度 low は対象外
            select 1 from spot_spatial x where x.spot_id = s.id and x.confidence = 'ok'
               and (p_open is null or sp_open(x.open_ratio) = p_open)                  -- open（開けている）/ closed（囲まれている）
               and (p_water is null or (p_water = 'near' and sp_water(x.water_dist_m) = 'near')       -- near（50m未満）
                                    or (p_water = 'within150' and sp_water(x.water_dist_m) in ('near','close')))   -- within150（150m未満）
               and (p_built is null or sp_built(x.building_cover_100) = p_built)                 -- few（少ない）/ many（多い）
               and (p_road is null or sp_road(x.road_class, x.road_width_m) = p_road)))  -- narrow（狭い道）/ wide
     and (p_tags is null or cardinality(p_tags) = 0 or exists (
            select 1 from spot_tags st where st.spot_id = s.id and st.tag_code = any (p_tags)))
     and (p_photo is null or photo_state(s.id, s.category, s.name like '（無名）%') = p_photo)    -- 写真の状態での絞り込み（ログイン時のみ画面から使う）
),
grouped as (
  select h.*,
         count(*) over w as same_line_count,
         row_number() over (w order by (h.confidence in ('desk_checked','field_checked')) desc,
                                       (h.position_kind = 'standpoint') desc, h.id) as rn
    from hits h
  -- 線の地点は同じ線ごと。名前のない地点と坂道は、同じ名前・同じ区ごとに代表点1件にまとめて「ほか○か所」（詳細で展開）
  window w as (partition by coalesce(h.line_key || '|' || h.area_code,
                                     case when h.unnamed or h.cat = 'slope_street'
                                          then h.cat || '|' || h.name || '|' || h.area_code end,
                                     h.id::text))
)
select coalesce(jsonb_agg(x), '[]'::jsonb) from (
  select g.id, g.code, g.name, g.area_code, g.address, g.category, g.grp_code, g.grp,
         g.era_feel, g.shoot_scope, g.status, g.confidence, g.data_status, g.summary, g.ffc_registered,
         g.position_kind, ST_Y(g.geom::geometry) as lat, ST_X(g.geom::geometry) as lon,
         g.relief_m, g.slope_grade_pct, g.slope_rise_m, g.fact_count,
         g.same_line_count - 1 as others,
         (select jsonb_agg(t.name order by st.score desc) from spot_tags st join tags t on t.code = st.tag_code
           where st.spot_id = g.id and st.score >= 0.5 and st.origin = 'human') as tags,   -- 公開の検索結果は人のタグだけ
         (select jsonb_agg(t.name order by t.code) from spot_tags st join tags t on t.code = st.tag_code
           where st.spot_id = g.id and st.origin = 'rule') as rule_tags,                    -- ルールで付けた（推定）。人のタグとは別に出す
         spatial_view(g.id) as spatial,
         photo_state(g.id, g.cat, g.unnamed) as photo_state,
         (select jsonb_build_object('url', ph.url, 'author', coalesce(ph.author, ph.shot_by), 'license', ph.license,
                                    'license_url', ph.license_url, 'source_url', ph.source_url, 'title', ph.title)
            from spot_photos ph where ph.spot_id = g.id order by ph.id limit 1) as photo,        -- 採用された写真（出典つき）
         exists (select 1 from change_risks rk where rk.spot_id = g.id and rk.risk_type <> 'none') as risk
    from grouped g
   where g.rn = 1
   order by g.unnamed,
            case when p_q is null then 0 else similarity(g.name, p_q) end desc,
            case when p_relief_min is null then 0 else g.relief_m end desc,
            (g.confidence in ('desk_checked','field_checked')) desc,
            (g.fact_count > 0 or g.summary is not null) desc,     -- 事実・説明文のある地点を先に
            md5(g.code)                                           -- 区・種類が偏らないよう散らす（毎回同じ順＝ページ送りは安定）
   offset p_offset limit p_limit
) x;
$$;

-- ロケハンリスト：指定した順で地点を返す
create function spots_by_ids(p_ids bigint[]) returns jsonb language sql stable as $$
select coalesce(jsonb_agg(x order by x.ord), '[]'::jsonb) from (
  select u.ord, s.id, s.code, s.name, s.area_code, s.address, sc.name as category, sc.landscape_group as grp_code,
         lg.name as grp, s.confidence, s.data_status, s.summary,
         (select count(*) from spot_facts f where f.spot_id = s.id and f.status = 'approved') as fact_count,
         ST_Y(s.geom::geometry) as lat, ST_X(s.geom::geometry) as lon,
         exists (select 1 from change_risks rk where rk.spot_id = s.id and rk.risk_type <> 'none') as risk
    from unnest(p_ids) with ordinality u(id, ord)
    join spots s on s.id = u.id
    join spot_categories sc on sc.code = s.category
    join landscape_groups lg on lg.code = sc.landscape_group
) x;
$$;

-- 地点の周辺（半径m以内）。同じ名前（川・商店街の流域の地点など）は最も近い1件だけ
create function spots_nearby(p_id bigint, p_radius real default 800)
returns jsonb language sql stable as $$
select coalesce(jsonb_agg(x order by x.meters), '[]'::jsonb) from (
  select * from (
    select distinct on (n.name, sc.landscape_group)
           n.id, n.name, lg.name as grp, sc.landscape_group as grp_code, sc.name as category,
           round(ST_Distance(n.geom, s.geom)) as meters,
           ST_Y(n.geom::geometry) as lat, ST_X(n.geom::geometry) as lon
      from spots s
      join spots n on n.id <> s.id and ST_DWithin(n.geom, s.geom, p_radius) and n.data_status <> 'archived'
      join spot_categories sc on sc.code = n.category
      join landscape_groups lg on lg.code = sc.landscape_group
     where s.id = p_id and n.name not like '（無名）%' and n.name <> s.name
     order by n.name, sc.landscape_group, ST_Distance(n.geom, s.geom)
  ) d
  order by d.meters
  limit 30
) x;
$$;

-- 地点の詳細（地点ページ用）
create function spot_detail(p_id bigint, p_with_history boolean default false)
returns jsonb language sql stable as $$
select jsonb_build_object(
  'id', s.id, 'code', s.code, 'name', s.name, 'alt_names', s.alt_names, 'address', s.address,
  'area_code', s.area_code, 'category', s.category, 'category_name', sc.name,
  'extra_categories', (select coalesce(jsonb_agg(c.name), '[]') from spot_categories c where c.code = any (s.extra_categories)),
  'grp_code', sc.landscape_group, 'grp', lg.name, 'shape', sc.shape,
  'lat', ST_Y(s.geom::geometry), 'lon', ST_X(s.geom::geometry),
  'line_lat', ST_Y(s.line_geom::geometry), 'line_lon', ST_X(s.line_geom::geometry),
  'position_kind', s.position_kind, 'line_key', s.line_key,
  'line_count', (select count(*) from spots o where o.line_key = s.line_key and o.data_status <> 'archived'),
  'status', s.status, 'confidence', s.confidence, 'data_status', s.data_status, 'review_reason', s.review_reason,
  'fact_count', (select count(*) from spot_facts f where f.spot_id = s.id and f.status = 'approved'),
  'photo_state', photo_state(s.id, s.category, s.name like '（無名）%'),
  'archived_reason', s.archived_reason, 'origin', s.origin, 'locked', s.locked,
  'era_feel', s.era_feel, 'shoot_scope', s.shoot_scope, 'owner_type', s.owner_type,
  'ffc_registered', s.ffc_registered, 'summary', s.summary, 'updated_at', s.updated_at,
  'created_at', s.created_at,
  'created_by', (select h.actor from spot_history h where h.spot_id = s.id and h.event = 'created' order by h.at limit 1),
  'osm', (select coalesce(jsonb_agg(jsonb_build_object('key', f.key, 'kind', f.kind, 'osm_type', f.osm_type,
                                                        'osm_id', f.osm_id, 'name', f.name, 'role', sf.role,
                                                        'way_ids', f.way_ids[1:5], 'tags', f.tags)
                                     order by sf.role, f.key), '[]')
            from spot_features sf join features f on f.key = sf.feature_key where sf.spot_id = s.id),
  'tags', (select coalesce(jsonb_agg(jsonb_build_object('code', t.code, 'name', t.name, 'facet', t.facet,
                                                         'origin', st.origin, 'score', st.score) order by st.score desc), '[]')
             from spot_tags st join tags t on t.code = st.tag_code where st.spot_id = s.id),
  'risks', (select coalesce(jsonb_agg(jsonb_build_object('type', r.risk_type, 'verified', r.verified,
                                                          'evidence', left(r.evidence, 300), 'url', r.source_url,
                                                          'at', r.detected_at) order by r.detected_at desc), '[]')
              from change_risks r where r.spot_id = s.id and r.risk_type <> 'none'),
  'practical', (select to_jsonb(p) - 'spot_id' from spot_practicals p where p.spot_id = s.id),
  'photos', (select coalesce(jsonb_agg(jsonb_build_object('url', ph.url, 'shot_at', ph.shot_at, 'by', ph.shot_by,
                                                          'author', ph.author, 'license', ph.license, 'license_url', ph.license_url,
                                                          'title', ph.title, 'source_url', ph.source_url) order by ph.id), '[]')
               from spot_photos ph where ph.spot_id = s.id),
  'sources', (select coalesce(jsonb_agg(jsonb_build_object('kind', so.kind, 'title', so.title, 'url', so.url,
                                                            'note', so.note, 'at', so.retrieved_at) order by so.retrieved_at desc), '[]')
                from sources so where so.spot_id = s.id),
  'flows', (select coalesce(jsonb_agg(distinct jsonb_build_object('id', c.id, 'name', c.name,
                                                                   'label', c.transition_label, 'mode', c.travel_mode)), '[]')
              from cluster_spots cs join clusters c on c.id = cs.cluster_id
             where cs.spot_id = s.id and c.status in ('reviewed','verified') and c.origin <> 'auto'),
  'history', case when p_with_history then
               (select coalesce(jsonb_agg(jsonb_build_object('at', h.at, 'event', h.event, 'actor', h.actor,
                                                              'detail', h.detail) order by h.at desc), '[]')
                  from (select * from spot_history where spot_id = s.id order by at desc limit 30) h)
             else null end
) || jsonb_build_object(        -- 地形・坂道（1つの jsonb_build_object の引数は100まで）
  'elevation_m', s.elevation_m, 'relief_m', s.relief_m, 'slope_pct', s.slope_pct,
  'terrain_src', s.terrain_src, 'terrain_status', s.terrain_status, 'terrain_at', s.terrain_at,
  'slope_len_m', s.slope_len_m, 'slope_grade_pct', s.slope_grade_pct, 'slope_rise_m', s.slope_rise_m,
  'slope_bearing', s.slope_bearing, 'slope_kind', s.slope_kind,
  'spatial', spatial_view(s.id),
  'summary_by', case when p_with_history then s.summary_by end, 'summary_at', s.summary_at,
  'drafts', case when p_with_history then
     (select coalesce(jsonb_agg(jsonb_build_object('id', n.id, 'source', n.source, 'body', n.body, 'model', n.model,
                                                   'prompt_version', n.prompt_version, 'at', n.created_at)
                                order by n.created_at desc), '[]')
        from spot_ai_notes n where n.spot_id = s.id and n.status = 'draft') end)
from spots s
join spot_categories sc on sc.code = s.category
join landscape_groups lg on lg.code = sc.landscape_group
where s.id = p_id;
$$;

-- 確認待ちの地点（ログイン後の「地点の確認」）
--  p_queue: review（要確認）/ unreviewed（未確認）/ manual（手入力・人が直した）/ archived（対象外）/ all
create function review_queue(p_area text default null, p_status text default null, p_q text default null,
                             p_offset int default 0, p_limit int default 50, p_queue text default 'review')
returns jsonb language sql stable as $$
select coalesce(jsonb_agg(x), '[]'::jsonb) from (
  select s.id, s.code, s.name, s.area_code, s.address, sc.name category, s.era_feel, s.shoot_scope,
         s.status, s.confidence, s.data_status, s.review_reason, s.archived_reason, s.summary, s.locked, s.origin,
         ST_Y(s.geom::geometry) lat, ST_X(s.geom::geometry) lon,
         (select jsonb_agg(jsonb_build_object('code', t.code, 'name', t.name, 'score', st.score, 'origin', st.origin)
                           order by st.score desc)
            from spot_tags st join tags t on t.code = st.tag_code where st.spot_id = s.id) tags,
         (select jsonb_agg(jsonb_build_object('id', r.id, 'type', r.risk_type, 'verified', r.verified))
            from change_risks r where r.spot_id = s.id) risks
    from spots s left join spot_categories sc on sc.code = s.category
   where (p_area is null or s.area_code = p_area)
     and (p_status is null or s.status = p_status)
     and (p_q is null or s.name ilike '%' || p_q || '%' or s.address ilike '%' || p_q || '%')
     and case coalesce(p_queue, 'review')
           when 'review' then s.data_status = 'needs_review'
           when 'archived' then s.data_status = 'archived'
           when 'manual' then s.data_status <> 'archived' and (s.origin = 'manual' or s.locked)
           when 'unreviewed' then s.data_status = 'current' and s.status in ('candidate','ai_tagged')
           else s.data_status <> 'archived' end
   order by s.code offset p_offset limit p_limit
) x;
$$;

-- 地点の確認結果を保存（人の判断。タグは origin='human' で保存しAI再実行で上書きされない）
create function review_spot(p_id bigint, p_status text, p_confidence text, p_summary text, p_tags text[],
                            p_reviewer text, p_resolve boolean default false)
returns void language plpgsql as $$
declare old record;
begin
  select status, confidence, data_status into old from spots where id = p_id;
  -- 説明文を保存した人が summary_by（公開してよい説明文＝人が書いた・採用したもの。130の制約）
  update spots set status = p_status, confidence = p_confidence, summary = coalesce(p_summary, summary),
                   summary_by = case when p_summary is not null then coalesce(p_reviewer, 'user') else summary_by end,
                   summary_at = case when p_summary is not null and p_summary is distinct from summary then now() else summary_at end,
                   data_status = case when p_resolve and data_status = 'needs_review' then 'current' else data_status end,
                   review_reason = case when p_resolve then null else review_reason end
   where id = p_id;
  if p_tags is not null then
    delete from spot_tags where spot_id = p_id and origin <> 'rule';      -- ルールのタグ（推定）は、人の保存で消さない
    insert into spot_tags (spot_id, tag_code, origin, score)
      select p_id, t, 'human', 1.0 from unnest(p_tags) t where exists (select 1 from tags where code = t)
      on conflict (spot_id, tag_code) do update set origin = 'human', score = 1.0;
  end if;
  insert into spot_history (spot_id, actor, event, detail)
  values (p_id, coalesce(p_reviewer, 'user'), 'reviewed',
          jsonb_build_object('from', jsonb_build_object('status', old.status, 'confidence', old.confidence,
                                                        'data_status', old.data_status),
                             'to', jsonb_build_object('status', p_status, 'confidence', p_confidence),
                             'resolved', p_resolve));
end $$;

-- 人による状態変更：archive（対象外にする）/ restore（戻す）/ resolve（要確認を解消）/ accept（OSMの変更を受け入れて位置を更新）
create function set_spot_state(p_id bigint, p_action text, p_user text, p_note text default null)
returns void language plpgsql as $$
begin
  if p_action = 'archive' then
    update spots set data_status = 'archived', archived_reason = 'manual:' || coalesce(p_note, ''), archived_at = now()
     where id = p_id;
  elsif p_action = 'restore' then
    update spots set data_status = 'current', archived_reason = null, archived_at = null, archived_run = null,
                     missing_runs = 0 where id = p_id;
  elsif p_action = 'resolve' then
    update spots set data_status = 'current', review_reason = null where id = p_id and data_status = 'needs_review';
  elsif p_action = 'lock' then
    update spots set locked = true where id = p_id;
  elsif p_action = 'unlock' then
    update spots set locked = false where id = p_id;
  else
    raise exception 'unknown action %', p_action;
  end if;
  insert into spot_history (spot_id, actor, event, detail)
  values (p_id, coalesce(p_user, 'user'), p_action, jsonb_build_object('note', p_note));
end $$;

-- 変化リスクの確認
create function review_risk(p_id bigint, p_verified boolean, p_dismiss boolean, p_note text)
returns void language plpgsql as $$
begin
  if p_dismiss then
    update change_risks set risk_type = 'none', verified = true,
           evidence = evidence || E'\n[該当なし] ' || coalesce(p_note, '') where id = p_id;
  else
    update change_risks set verified = p_verified, certainty = case when p_verified then 'confirmed' else certainty end,
           evidence = evidence || E'\n[確認] ' || coalesce(p_note, '') where id = p_id;
  end if;
end $$;

-- 一時停止防止・死活確認
create function ping() returns jsonb language sql stable as $$
  select jsonb_build_object('spots', (select count(*) from spots where data_status <> 'archived'),
                            'clusters', (select count(*) from clusters), 'at', now());
$$;

-- 重複の疑い：同じ名前（正規化）・同じ景観で近すぎる、まとめられていない点の組
create function duplicate_candidates(p_limit int default 100, p_meters real default 150)
returns jsonb language sql stable as $$
select coalesce(jsonb_agg(x), '[]'::jsonb) from (
  select a.id a_id, a.code a_code, a.name a_name, b.id b_id, b.code b_code, b.name b_name,
         sca.name category, round(ST_Distance(a.geom, b.geom)) meters, a.area_code,
         ST_Y(a.geom::geometry) a_lat, ST_X(a.geom::geometry) a_lon, ST_Y(b.geom::geometry) b_lat, ST_X(b.geom::geometry) b_lon
    from spots a
    join spots b on b.id > a.id and ST_DWithin(a.geom, b.geom, p_meters)
    join spot_categories sca on sca.code = a.category
    join spot_categories scb on scb.code = b.category
   where a.data_status <> 'archived' and b.data_status <> 'archived'
     and a.sample_key is null and b.sample_key is null
     and a.name not like '（無名）%'
     and regexp_replace(lower(a.name), '[\s・()（）]', '', 'g') = regexp_replace(lower(b.name), '[\s・()（）]', '', 'g')
     and sca.landscape_group = scb.landscape_group
   order by ST_Distance(a.geom, b.geom)
   limit p_limit
) x;
$$;

-- 2つの地点を1つにまとめる（p_drop を p_keep に寄せる。p_drop は対象外にして履歴を残す）
create function merge_spots(p_keep bigint, p_drop bigint, p_user text)
returns void language plpgsql as $$
begin
  if p_keep = p_drop then raise exception 'same spot'; end if;
  insert into spot_features (spot_id, feature_key, role)
    select p_keep, feature_key, 'secondary' from spot_features where spot_id = p_drop
  on conflict do nothing;
  delete from spot_features where spot_id = p_drop;
  insert into spot_tags (spot_id, tag_code, origin, score)
    select p_keep, tag_code, origin, score from spot_tags where spot_id = p_drop
  on conflict do nothing;
  update sources set spot_id = p_keep where spot_id = p_drop;
  update change_risks set spot_id = p_keep where spot_id = p_drop;
  update cluster_spots set spot_id = p_keep where spot_id = p_drop
     and not exists (select 1 from cluster_spots c2 where c2.cluster_id = cluster_spots.cluster_id and c2.spot_id = p_keep);
  update spots set alt_names = (select array_agg(distinct x) from unnest(spots.alt_names ||
                                 (select array[name] || alt_names from spots where id = p_drop)) x
                                 where x <> spots.name)
   where id = p_keep;
  update spots set data_status = 'archived', archived_reason = 'manual:merged into ' || p_keep, archived_at = now()
   where id = p_drop;
  insert into spot_history (spot_id, actor, event, detail) values
    (p_keep, coalesce(p_user, 'user'), 'merged', jsonb_build_object('absorbed', p_drop)),
    (p_drop, coalesce(p_user, 'user'), 'merged_into', jsonb_build_object('into', p_keep));
end $$;

-- 診断（Actions「診断」と画面の両方から使う）
create function diag_summary() returns jsonb language sql stable as $$
select jsonb_build_object(
  'at', now(),
  'spots_live', (select count(*) from spots where data_status <> 'archived'),
  'spots_by_data_status', (select jsonb_object_agg(data_status, n) from (select data_status, count(*) n from spots group by 1) x),
  'spots_by_status', (select jsonb_object_agg(status, n) from (select status, count(*) n from spots where data_status <> 'archived' group by 1) x),
  'protected', (select count(*) from spots where origin = 'manual' or locked or status in ('scouted','verified')
                   or confidence in ('desk_checked','field_checked')),
  'by_category', (select jsonb_object_agg(category, n) from (select category, count(*) n from spots where data_status <> 'archived' group by 1) x),
  'by_area', (select jsonb_object_agg(coalesce(area_code, '(none)'), n) from (select area_code, count(*) n from spots where data_status <> 'archived' group by 1) x),
  'no_address', (select count(*) from spots where data_status <> 'archived' and address is null),
  'ai_notes', (select coalesce(jsonb_object_agg(k, n), '{}') from
                 (select source || ':' || status k, count(*) n from spot_ai_notes group by 1) x),
  'public_summaries', (select count(*) from spots where summary is not null and data_status <> 'archived'),
  'terrain', (select jsonb_build_object('ok', count(*) filter (where terrain_status = 'ok'),
                                        'no_data', count(*) filter (where terrain_status = 'no_data'),
                                        'pending', count(*) filter (where terrain_status is null))
                from spots where data_status <> 'archived'),
  'orphan_links', (select count(*) from spot_features sf where not exists (select 1 from features f where f.key = sf.feature_key)),
  'spots_without_feature', (select count(*) from spots s where s.origin = 'osm' and not exists (select 1 from spot_features sf where sf.spot_id = s.id)),
  'area_mismatch', (select count(*) from spots s join areas a on a.code = s.area_code
                     where a.geom is not null and s.data_status <> 'archived' and not ST_Covers(a.geom, s.geom)),
  'duplicate_pairs', (select jsonb_array_length(duplicate_candidates(1000, 150))),
  'features_missing', (select count(*) from features where missing_runs > 0),
  'clusters_needing_review', (select count(*) from clusters where needs_review and status <> 'archived'),
  'clusters_with_archived_spot', (select count(*) from clusters c where c.status <> 'archived' and exists (
       select 1 from cluster_spots cs join spots s on s.id = cs.spot_id where cs.cluster_id = c.id and s.data_status = 'archived')),
  'last_runs', (select jsonb_agg(jsonb_build_object('id', id, 'mode', mode, 'status', status, 'started', started_at,
                                                     'finished', finished_at, 'osm', source ->> 'osm_timestamp',
                                                     'code', code_version, 'rules', rules_version, 'summary', summary)
                                  order by id desc)
                  from (select * from data_runs order by id desc limit 5) r)
);
$$;
