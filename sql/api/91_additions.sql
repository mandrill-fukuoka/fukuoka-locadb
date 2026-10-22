-- 追加候補（DB変更203。tools/migrate.py が毎回作り直す）
--  確認画面の「追加候補」タブは additions_counts と additions_list で読み、addition_decide で採否を決める（人だけ）
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in ('additions_counts','additions_list','addition_decide')
  loop execute format('drop function %s', f.sig); end loop;
end $$;

create function additions_counts() returns jsonb language sql stable as $$
select jsonb_build_object(
  'candidate', (select count(*) from spot_additions where status = 'candidate'),
  'adopted',   (select count(*) from spot_additions where status = 'adopted'),
  'rejected',  (select count(*) from spot_additions where status = 'rejected'),
  'wikipedia', (select count(*) from spot_additions where status = 'candidate' and source = 'wikipedia'),
  'commons',   (select count(*) from spot_additions where status = 'candidate' and source = 'commons'));
$$;

create function additions_list(p_status text default 'candidate', p_source text default null, p_limit int default 30, p_offset int default 0)
returns jsonb language sql stable as $$
select coalesce(jsonb_agg(x), '[]'::jsonb) from (
  select a.id, a.source, a.name, a.lat, a.lon, a.area_code, ar.name as area_name, a.suggest_cat, a.source_url,
         a.files, a.samples, a.near_name, round(a.near_m::numeric) as near_m, a.status, a.spot_id,
         s.code as spot_code, a.decided_by, a.decided_at
    from spot_additions a
    left join areas ar on ar.code = a.area_code
    left join spots s on s.id = a.spot_id
   where a.status = p_status and (p_source is null or a.source = p_source)
   order by a.source, coalesce(a.files, 0) desc, a.id
   offset p_offset limit least(p_limit, 100)
) x;
$$;

-- 採否を決める。p_decision：'adopt'（採用：手入力の地点を作る。名前・種別は人が確かめた値）/ 'reject'（不採用）/ 'reset'（不採用を候補に戻す）
--  採用は create_manual_spot を使う（区の判定・コード採番・履歴・出典の記録。確度は「未確認」）。作った地点は、地点の画面で直す
create function addition_decide(p_id bigint, p_decision text, p_user text, p_name text default null,
                                p_category text default null) returns jsonb language plpgsql as $$
declare a spot_additions; r jsonb; v_name text;
begin
  select * into a from spot_additions where id = p_id for update;
  if not found then return jsonb_build_object('ok', false, 'reason', '候補がありません'); end if;
  if coalesce(p_user, '') = '' then return jsonb_build_object('ok', false, 'reason', '決める人の名前が必要です'); end if;
  if p_decision = 'adopt' then
    if a.status = 'adopted' then return jsonb_build_object('ok', false, 'reason', 'すでに採用済みです'); end if;
    v_name := btrim(coalesce(nullif(btrim(coalesce(p_name, '')), ''), a.name));
    if v_name = '' or v_name like '（名称未設定）%' then
      return jsonb_build_object('ok', false, 'reason', '地点の名前を入力してください');
    end if;
    if coalesce(p_category, '') = '' then return jsonb_build_object('ok', false, 'reason', '種別を選んでください'); end if;
    r := create_manual_spot(v_name, a.lat, a.lon, p_category, '{}', null, 'unverified', a.source_url,
                            case a.source when 'wikipedia' then 'Wikipedia（日本語）の座標つき記事から追加候補として採用'
                                 else 'Wikimedia Commons の写真が集まる場所から追加候補として採用' end,
                            'official', null, p_user);
    update spot_additions set status = 'adopted', spot_id = (r->>'id')::bigint, decided_by = p_user, decided_at = now() where id = p_id;
    return jsonb_build_object('ok', true, 'spot_id', (r->>'id')::bigint, 'code', r->>'code');
  elsif p_decision = 'reject' then
    if a.status = 'adopted' then return jsonb_build_object('ok', false, 'reason', '採用済みです（地点の画面で対象外にできます）'); end if;
    update spot_additions set status = 'rejected', decided_by = p_user, decided_at = now() where id = p_id;
  elsif p_decision = 'reset' then
    if a.status = 'adopted' then return jsonb_build_object('ok', false, 'reason', '採用済みは戻せません（地点の画面で直します）'); end if;
    update spot_additions set status = 'candidate', decided_by = null, decided_at = null where id = p_id;
  else
    return jsonb_build_object('ok', false, 'reason', '決め方が正しくありません');
  end if;
  return jsonb_build_object('ok', true);
exception when others then
  return jsonb_build_object('ok', false, 'reason', sqlerrm);
end $$;
