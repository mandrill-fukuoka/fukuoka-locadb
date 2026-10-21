-- 写真候補（DB変更200。tools/migrate.py が毎回作り直す）
--  確認画面の「写真候補」タブは photos_counts と photo_candidates_list で読み、photo_decide で採否を決める（人だけ）
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in ('photos_counts','photo_candidates_list','photo_decide')
  loop execute format('drop function %s', f.sig); end loop;
end $$;

create function photos_counts() returns jsonb language sql stable as $$
select jsonb_build_object(
  'candidate', (select count(*) from spot_photo_candidates c join spots s on s.id = c.spot_id
                 where c.status = 'candidate' and s.data_status <> 'archived'),
  'adopted',   (select count(*) from spot_photo_candidates where status = 'adopted'),
  'rejected',  (select count(*) from spot_photo_candidates where status = 'rejected'),
  'spots',     (select count(distinct c.spot_id) from spot_photo_candidates c join spots s on s.id = c.spot_id
                 where c.status = 'candidate' and s.data_status <> 'archived'));
$$;

-- 候補の一覧（地点ごとにまとめて見られるよう、地点・距離の順）
create function photo_candidates_list(p_status text default 'candidate', p_limit int default 30, p_offset int default 0)
returns jsonb language sql stable as $$
select coalesce(jsonb_agg(x), '[]'::jsonb) from (
  select c.id, c.spot_id, s.name, s.code, sc.name as category, s.address,
         c.file_title, c.page_url, c.image_url, c.author, c.license, c.license_url, c.description, c.shot_at,
         round(c.dist_m::numeric) as dist_m, c.name_match, c.status, c.decided_by, c.decided_at
    from spot_photo_candidates c
    join spots s on s.id = c.spot_id and s.data_status <> 'archived'
    join spot_categories sc on sc.code = s.category
   where c.status = p_status
   order by c.name_match desc, s.id, c.dist_m nulls last, c.id
   offset p_offset limit least(p_limit, 100)
) x;
$$;

-- 採否を決める。p_decision は 'adopt'（採用：spot_photos に出典つきで入れる）/ 'reject'（不採用）/ 'reset'（候補に戻す。採用済みなら写真を外す）
create function photo_decide(p_id bigint, p_decision text, p_user text) returns jsonb language plpgsql as $$
declare c spot_photo_candidates;
begin
  select * into c from spot_photo_candidates where id = p_id;
  if not found then return jsonb_build_object('ok', false, 'reason', '候補がありません'); end if;
  if p_decision = 'adopt' then
    if coalesce(p_user, '') = '' then return jsonb_build_object('ok', false, 'reason', '採用する人の名前が必要です'); end if;
    if not exists (select 1 from spot_photos where spot_id = c.spot_id and source_url = c.page_url) then
      insert into spot_photos (spot_id, url, shot_by, license, author, title, source_url, license_url)
      values (c.spot_id, c.image_url, c.author, c.license, c.author, c.file_title, c.page_url, c.license_url);
    end if;
    update spot_photo_candidates set status = 'adopted', decided_by = p_user, decided_at = now() where id = p_id;
  elsif p_decision = 'reject' then
    update spot_photo_candidates set status = 'rejected', decided_by = p_user, decided_at = now() where id = p_id;
  elsif p_decision = 'reset' then
    delete from spot_photos where spot_id = c.spot_id and source_url = c.page_url;
    update spot_photo_candidates set status = 'candidate', decided_by = null, decided_at = null where id = p_id;
  else
    return jsonb_build_object('ok', false, 'reason', '決め方が正しくありません');
  end if;
  return jsonb_build_object('ok', true, 'spot_id', c.spot_id);
end $$;
