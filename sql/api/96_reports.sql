-- 誤りの連絡口（順5、DB変更280。tools/migrate.py が毎回作り直す）
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in ('report_submit','reports_list','report_handle')
  loop execute format('drop function %s', f.sig); end loop;
end $$;

-- 連絡を1件受ける（ログインなしの画面から。Web側で回数制限・CSRF確認を済ませてから呼ぶ）
create function report_submit(p_spot_id bigint, p_kind text, p_note text) returns bigint language plpgsql as $$
declare v_id bigint; v_name text;
begin
  if p_kind not in ('wrong_place','wrong_name','closed','wrong_info','photo','other') then
    raise exception '連絡の種類が正しくありません';
  end if;
  if p_note is null or length(btrim(p_note)) = 0 then raise exception '内容を入れてください'; end if;
  select s.name into v_name from spots s where s.id = p_spot_id;
  if p_spot_id is not null and v_name is null then raise exception '地点が見つかりません'; end if;
  insert into spot_reports (spot_id, spot_name, kind, note)
  values (p_spot_id, v_name, p_kind, left(btrim(p_note), 1000)) returning id into v_id;
  return v_id;
end $$;

-- 連絡の一覧（ログインした人用）。p_status：open／done／dismissed／all
create function reports_list(p_status text default 'open', p_limit int default 100) returns jsonb language sql stable as $$
select coalesce(jsonb_agg(to_jsonb(x) order by x.created_at desc), '[]'::jsonb) from (
  select r.id, r.spot_id, coalesce(s.name, r.spot_name) as spot_name, r.kind, r.note, r.status,
         r.created_at, r.handled_by, r.handled_at, r.handled_note
    from spot_reports r left join spots s on s.id = r.spot_id
   where p_status = 'all' or r.status = coalesce(p_status, 'open')
   order by r.created_at desc limit greatest(least(p_limit, 500), 1)
) x;
$$;

-- 連絡を処理済みにする（done：直した／dismissed：取り合わない）
create function report_handle(p_id bigint, p_status text, p_user text, p_note text default null) returns void language plpgsql as $$
begin
  if p_status not in ('open','done','dismissed') then raise exception '状態が正しくありません'; end if;
  update spot_reports set status = p_status,
         handled_by = case when p_status = 'open' then null else p_user end,
         handled_at = case when p_status = 'open' then null else now() end,
         handled_note = case when p_status = 'open' then null else nullif(btrim(p_note), '') end
   where id = p_id;
  if not found then raise exception '連絡が見つかりません'; end if;
end $$;
