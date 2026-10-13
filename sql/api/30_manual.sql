-- 画面（PHP）から呼ぶ関数：地点の手入力・修正・撮影実務（タスクA）。tools/migrate.py が毎回作り直す
--  - 手入力の地点は origin='manual'。自動収集では動かさず、消さず、AI分類もしない（sync.py・classify.py）
--  - OSMの地点を人が直したときは locked=true にし、直す前の値を osm_seen に残す
--    （収集は osm_seen と比べるので、人が直したことだけでは要確認にならない）
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in (
              'area_at','manual_check','create_manual_spot','edit_spot','save_practicals')
  loop execute format('drop function %s', f.sig); end loop;
end $$;

-- 位置がどの区か（7区の外なら null）
create function area_at(p_lat float8, p_lon float8) returns text language sql stable as $$
  select a.code from areas a
   where a.geom is not null and ST_Covers(a.geom, ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326)::geography)
   order by a.code limit 1;
$$;

-- 追加・修正の前の確認：区と、近く（150m以内）の地点。似た名前は similar=true（重複の防止）
create function manual_check(p_lat float8, p_lon float8, p_name text default null, p_exclude bigint default null)
returns jsonb language sql stable as $$
with p as (select ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326)::geography g,
                  nullif(regexp_replace(lower(coalesce(p_name, '')), '[\s・()（）]', '', 'g'), '') nn)
select jsonb_build_object(
  'area_code', area_at(p_lat, p_lon),
  'nearby', (select coalesce(jsonb_agg(x order by x.similar desc, x.meters), '[]'::jsonb) from (
      select s.id, s.code, s.name, sc.name as category, sc.landscape_group as grp_code, s.origin,
             round(ST_Distance(s.geom, p.g)) as meters,
             (p.nn is not null and (regexp_replace(lower(s.name), '[\s・()（）]', '', 'g') = p.nn
                                    or similarity(s.name, p_name) > 0.4
                                    or exists (select 1 from unnest(s.alt_names) a
                                                where regexp_replace(lower(a), '[\s・()（）]', '', 'g') = p.nn))) as similar
        from spots s join spot_categories sc on sc.code = s.category, p
       where ST_DWithin(s.geom, p.g, 150) and s.data_status <> 'archived'
         and (p_exclude is null or s.id <> p_exclude)
       order by ST_Distance(s.geom, p.g)
       limit 20) x)
) from p;
$$;

-- 手入力の地点を作る。コードは区ごとの FUK-<区>-M0001（OSMの地点の S 連番とは別系列）
create function create_manual_spot(
  p_name text, p_lat float8, p_lon float8, p_category text, p_extra text[] default '{}',
  p_summary text default null, p_confidence text default 'unverified',
  p_source_url text default null, p_source_note text default null, p_source_kind text default 'official',
  p_address text default null, p_user text default null
) returns jsonb language plpgsql as $$
declare
  v_name text := btrim(coalesce(p_name, ''));
  v_area text; v_prefix text; v_n int; v_code text; v_id bigint;
  v_extra text[];
begin
  if v_name = '' or length(v_name) > 100 then raise exception '名前を1〜100文字で入力してください'; end if;
  if p_lat is null or p_lon is null then raise exception '位置を指定してください'; end if;
  if not exists (select 1 from spot_categories where code = p_category) then raise exception '種別を選んでください'; end if;
  if coalesce(p_confidence, 'unverified') not in ('unverified','desk_checked','field_checked') then
    raise exception '確度の値が正しくありません';
  end if;
  v_extra := array(select distinct e from unnest(coalesce(p_extra, '{}')) e
                    where e <> p_category and exists (select 1 from spot_categories where code = e));
  v_area := area_at(p_lat, p_lon);
  if v_area is null then raise exception 'この位置は対象エリア（福岡市7区）の外です'; end if;

  perform pg_advisory_xact_lock(hashtext('manual_spot_code'));
  v_prefix := 'FUK-' || upper(left(split_part(v_area, '-', 2), 3)) || '-M';
  select coalesce(max(substr(code, length(v_prefix) + 1)::int), 0) + 1 into v_n
    from spots where code like v_prefix || '%' and substr(code, length(v_prefix) + 1) ~ '^\d+$';
  v_code := v_prefix || lpad(v_n::text, 4, '0');

  insert into spots (code, name, area_code, geom, category, extra_categories, position_kind, origin,
                     confidence, summary, address, address_at)
  values (v_code, v_name, v_area, ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326)::geography, p_category, v_extra,
          'manual', 'manual', coalesce(p_confidence, 'unverified'), nullif(btrim(coalesce(p_summary, '')), ''),
          nullif(p_address, ''), case when nullif(p_address, '') is null then null else now() end)
  returning id into v_id;

  if nullif(btrim(coalesce(p_source_url, '')), '') is not null or nullif(btrim(coalesce(p_source_note, '')), '') is not null then
    insert into sources (spot_id, kind, title, url, note)
    values (v_id, case when p_source_kind in ('official','news','field','review') then p_source_kind else 'official' end,
            null, nullif(btrim(coalesce(p_source_url, '')), ''), nullif(btrim(coalesce(p_source_note, '')), ''));
  end if;

  insert into spot_history (spot_id, actor, event, detail)
  values (v_id, coalesce(p_user, 'user'), 'created',
          jsonb_build_object('manual', true, 'category', p_category, 'lon', p_lon, 'lat', p_lat));
  return jsonb_build_object('id', v_id, 'code', v_code, 'area_code', v_area);
end $$;

-- 位置・名前・種別を直す（手入力の地点・OSMの地点のどちらも）。null の項目は変えない。
--  OSMの地点は locked=true にし、直す前の値を osm_seen に残す（次の収集で要確認にならない）
--  位置を動かしたら区を判定し直す（コードは変えない）。住所は p_address、なければ次の収集で取り直す
create function edit_spot(
  p_id bigint, p_name text default null, p_lat float8 default null, p_lon float8 default null,
  p_category text default null, p_extra text[] default null, p_address text default null,
  p_user text default null, p_note text default null
) returns jsonb language plpgsql as $$
declare
  o record; v_name text := nullif(btrim(coalesce(p_name, '')), '');
  v_changed text[] := '{}'; v_area text; v_m real; v_extra text[]; v_actor text := coalesce(p_user, 'user');
begin
  select s.*, ST_X(s.geom::geometry) lon, ST_Y(s.geom::geometry) lat into o from spots s where s.id = p_id for update;
  if not found then raise exception '地点が見つかりません'; end if;

  -- 名前
  if v_name is not null and v_name <> o.name then
    if length(v_name) > 100 then raise exception '名前は100文字までです'; end if;
    update spots set name = v_name,
                     alt_names = case when o.name like '（無名）%' or o.name = any (alt_names) then alt_names
                                      else array_append(alt_names, o.name) end
     where id = p_id;
    insert into spot_history (spot_id, actor, event, detail)
    values (p_id, v_actor, 'renamed', jsonb_build_object('from', o.name, 'to', v_name, 'note', p_note, 'by_hand', true));
    v_changed := array_append(v_changed, 'name');
  end if;

  -- 位置
  if p_lat is not null and p_lon is not null then
    v_m := ST_Distance(o.geom, ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326)::geography);
    if v_m >= 1 then
      v_area := area_at(p_lat, p_lon);
      if v_area is null then raise exception 'この位置は対象エリア（福岡市7区）の外です'; end if;
      update spots set geom = ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326)::geography,
                       position_kind = 'manual',
                       address = nullif(p_address, ''),
                       address_at = case when nullif(p_address, '') is null then null else now() end
       where id = p_id;
      insert into spot_history (spot_id, actor, event, detail)
      values (p_id, v_actor, 'moved', jsonb_build_object('from', jsonb_build_array(o.lon, o.lat),
                                                          'to', jsonb_build_array(p_lon, p_lat),
                                                          'm', round(v_m), 'note', p_note, 'by_hand', true));
      if v_area is distinct from o.area_code then
        update spots set area_code = v_area where id = p_id;
        insert into spot_history (spot_id, actor, event, detail)
        values (p_id, v_actor, 'area', jsonb_build_object('from', o.area_code, 'to', v_area));
      end if;
      v_changed := array_append(v_changed, 'position');
    end if;
  end if;

  -- 種別
  if p_category is not null then
    if not exists (select 1 from spot_categories where code = p_category) then raise exception '種別を選んでください'; end if;
    v_extra := array(select distinct e from unnest(coalesce(p_extra, o.extra_categories)) e
                      where e <> p_category and exists (select 1 from spot_categories where code = e) order by e);
    if p_category <> o.category or v_extra <> array(select e from unnest(o.extra_categories) e order by e) then
      update spots set category = p_category, extra_categories = v_extra where id = p_id;
      insert into spot_history (spot_id, actor, event, detail)
      values (p_id, v_actor, 'recategorized', jsonb_build_object('from', to_jsonb(o.category || o.extra_categories),
                                                                  'to', to_jsonb(p_category || v_extra),
                                                                  'note', p_note, 'by_hand', true));
      v_changed := array_append(v_changed, 'category');
    end if;
  end if;

  -- OSMの地点を人が直した → 自動では変えない。直す前の値（＝その時点のOSMの値）を比較の基準に残す
  if cardinality(v_changed) > 0 and o.origin = 'osm' then
    update spots set locked = true,
                     osm_seen = coalesce(osm_seen, jsonb_build_object(
                       'name', case when o.name like '（無名）%' then null else o.name end,
                       'lon', o.lon, 'lat', o.lat,
                       'cats', to_jsonb(o.category || array(select e from unnest(o.extra_categories) e order by e))))
     where id = p_id;
    if not o.locked then
      insert into spot_history (spot_id, actor, event, detail)
      values (p_id, v_actor, 'lock', jsonb_build_object('note', '人が直したため自動では変えない'));
    end if;
  end if;
  return jsonb_build_object('changed', to_jsonb(v_changed),
                            'area_code', (select area_code from spots where id = p_id));
end $$;

-- 撮影実務（許可の要否・相談先・制約・撮影実績・メモ）。空欄は null。どの地点にも入力できる
create function save_practicals(
  p_id bigint, p_permit_required text, p_permit_contact text, p_restrictions text,
  p_past_productions jsonb, p_notes text, p_user text default null
) returns void language plpgsql as $$
begin
  if not exists (select 1 from spots where id = p_id) then raise exception '地点が見つかりません'; end if;
  if p_permit_required is not null and p_permit_required not in ('required','consult','not_required','unknown') then
    raise exception '許可の要否の値が正しくありません';
  end if;
  insert into spot_practicals (spot_id, permit_required, permit_contact, restrictions, past_productions, notes,
                               updated_at, updated_by)
  values (p_id, p_permit_required, nullif(btrim(coalesce(p_permit_contact, '')), ''),
          nullif(btrim(coalesce(p_restrictions, '')), ''), coalesce(p_past_productions, '[]'::jsonb),
          nullif(btrim(coalesce(p_notes, '')), ''), now(), coalesce(p_user, 'user'))
  on conflict (spot_id) do update set
    permit_required = excluded.permit_required, permit_contact = excluded.permit_contact,
    restrictions = excluded.restrictions, past_productions = excluded.past_productions,
    notes = excluded.notes, updated_at = excluded.updated_at, updated_by = excluded.updated_by;
  insert into spot_history (spot_id, actor, event, detail)
  values (p_id, coalesce(p_user, 'user'), 'practicals', '{}'::jsonb);
end $$;
