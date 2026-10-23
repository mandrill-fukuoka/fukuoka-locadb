-- 写真候補（DB変更200。tools/migrate.py が毎回作り直す）
--  確認画面の「写真候補」タブは photos_counts と photo_candidates_list で読み、photo_decide で採否を決める（人だけ）
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in ('photos_counts','photo_candidates_list','photo_decide','photo_suggest','photos_suggest_counts','photo_decide_bulk')
  loop execute format('drop function %s', f.sig); end loop;
end $$;

-- おすすめ度（DB変更206。暫定のルール＝人が決めた125件の傾向から。AIは使わない）
--  low  ：説明文・ファイル名に看板・標識・案内・駅名標・地図・ポスターなどの語（決めた10件は全て不採用）
--  high ：Commons で、名前が一致し、low でないもの（決めた115件のうち採用108件）
--  unknown：それ以外（Mapillary・名前が一致しない写真は、人が決めた例がまだないので判断材料なし）
--  印は並べ替えと絞り込み、まとめて採用の対象を決めるためだけに使う。採用・不採用を決めるのは人（photo_decide / photo_decide_bulk）
create function photo_suggest(p_source text, p_name_match boolean, p_description text, p_title text) returns text
language sql immutable as $$
select case
  when coalesce(p_description, '') ~* '(看板|標識|案内|駅名標|地図|ポスター|\msigns?\M|\mplaque\M|\mmap\M|\mposter\M)'
    or coalesce(p_title, '') ~* '(看板|標識|案内|駅名標|地図|ポスター|\msigns?\M|\mplaque\M|\mmap\M|\mposter\M)' then 'low'
  when p_source = 'commons' and p_name_match then 'high'
  else 'unknown' end;
$$;

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
create function photo_candidates_list(p_status text default 'candidate', p_limit int default 30, p_offset int default 0,
                                     p_suggest text default null)
returns jsonb language sql stable as $$
select coalesce(jsonb_agg(x), '[]'::jsonb) from (
  select c.id, c.spot_id, s.name, s.code, sc.name as category, s.address,
         c.file_title, c.page_url, c.image_url, c.author, c.license, c.license_url, c.description, c.shot_at,
         round(c.dist_m::numeric) as dist_m, c.name_match, c.status, c.decided_by, c.decided_at, c.reject_reason,
         photo_suggest(c.source, c.name_match, c.description, c.file_title) as suggest
    from spot_photo_candidates c
    join spots s on s.id = c.spot_id and s.data_status <> 'archived'
    join spot_categories sc on sc.code = s.category
   where c.status = p_status
     and (p_suggest is null or photo_suggest(c.source, c.name_match, c.description, c.file_title) = p_suggest)
   order by (case photo_suggest(c.source, c.name_match, c.description, c.file_title) when 'high' then 0 when 'unknown' then 1 else 2 end),
            c.name_match desc, s.id, c.dist_m nulls last, c.id
   offset p_offset limit least(p_limit, 100)
) x;
$$;

-- 採否を決める。p_decision は 'adopt'（採用：spot_photos に出典つきで入れる）/ 'reject'（不採用）/ 'reset'（候補に戻す。採用済みなら写真を外す）
-- p_archive：理由が closed（現存しない）のときだけ効く。地点を「対象外」にし、同じ地点の未決の候補もまとめて不採用にする（地点は消さない）
create function photo_decide(p_id bigint, p_decision text, p_user text, p_reason text default null, p_archive boolean default false) returns jsonb language plpgsql as $$
declare c spot_photo_candidates; archived boolean := false;
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
    if coalesce(p_reason, '') not in ('', 'sign', 'person', 'quality', 'wrong_place', 'rights', 'other', 'closed') then
      return jsonb_build_object('ok', false, 'reason', '不採用の理由が正しくありません');
    end if;
    update spot_photo_candidates set status = 'rejected', decided_by = p_user, decided_at = now(), reject_reason = nullif(p_reason, '') where id = p_id;
    if p_reason = 'closed' and coalesce(p_archive, false) then
      if coalesce(p_user, '') = '' then return jsonb_build_object('ok', false, 'reason', '地点を対象外にする人の名前が必要です'); end if;
      perform set_spot_state(c.spot_id, 'archive', p_user, '現存しない（写真の確認で判明）');
      update spot_photo_candidates set status = 'rejected', decided_by = p_user, decided_at = now(), reject_reason = 'closed'
       where spot_id = c.spot_id and status = 'candidate';
      archived := true;
    end if;
  elsif p_decision = 'reset' then
    delete from spot_photos where spot_id = c.spot_id and source_url = c.page_url;
    update spot_photo_candidates set status = 'candidate', decided_by = null, decided_at = null, reject_reason = null where id = p_id;
  else
    return jsonb_build_object('ok', false, 'reason', '決め方が正しくありません');
  end if;
  return jsonb_build_object('ok', true, 'spot_id', c.spot_id, 'archived', archived);
end $$;

-- おすすめ度ごとの件数（未決の候補）
create function photos_suggest_counts() returns jsonb language sql stable as $$
select coalesce(jsonb_object_agg(k, n), '{}'::jsonb) from (
  select photo_suggest(c.source, c.name_match, c.description, c.file_title) k, count(*) n
    from spot_photo_candidates c join spots s on s.id = c.spot_id and s.data_status <> 'archived'
   where c.status = 'candidate' group by 1) x;
$$;

-- まとめて決める（人が一覧を見て押したときだけ）。p_ids のうち、まだ未決のものだけを対象にする。
--  採用（adopt）は、おすすめ度が「見込み高」のものだけ（サーバー側でも確かめる）。不採用（reject）は理由つきで
create function photo_decide_bulk(p_ids bigint[], p_decision text, p_user text, p_reason text default null) returns jsonb
language plpgsql as $$
declare c record; r jsonb; n_ok int := 0; n_skip int := 0;
begin
  if coalesce(p_user, '') = '' then return jsonb_build_object('ok', false, 'reason', '決める人の名前が必要です'); end if;
  if p_decision not in ('adopt', 'reject') then return jsonb_build_object('ok', false, 'reason', '決め方が正しくありません'); end if;
  for c in select * from spot_photo_candidates where id = any (p_ids) and status = 'candidate' order by id loop
    if p_decision = 'adopt' and photo_suggest(c.source, c.name_match, c.description, c.file_title) <> 'high' then
      n_skip := n_skip + 1; continue;
    end if;
    r := photo_decide(c.id, p_decision, p_user, p_reason);
    if (r->>'ok')::boolean then n_ok := n_ok + 1; else n_skip := n_skip + 1; end if;
  end loop;
  return jsonb_build_object('ok', true, 'decided', n_ok, 'skipped', n_skip);
end $$;
