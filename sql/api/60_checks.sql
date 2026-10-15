-- 登録地の自動チェックの指摘を画面に出す・閉じる（タスクG、DB変更170。tools/migrate.py が毎回作り直す）
--  指摘を書くのは locadb/verify.py（apply）だけ。画面は読むのと「問題なし」で閉じるだけ
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in ('checks_counts','checks_list','check_close')
  loop execute format('drop function %s', f.sig); end loop;
end $$;

-- 未対応の指摘の件数（チェックごと）と、最後に確かめた日時
create function checks_counts() returns jsonb language sql stable as $$
select jsonb_build_object(
  'items', coalesce((select jsonb_agg(x order by x.target, x.severity desc, x.n desc) from (
      select c.target, c.check_code, max(c.label) as label, c.severity, count(*) as n, count(distinct c.spot_id) as spots
        from spot_checks c join spots s on s.id = c.spot_id
       where c.status = 'open' and s.data_status <> 'archived'
       group by c.target, c.check_code, c.severity) x), '[]'::jsonb),
  'last_run', (select max(last_seen) from spot_checks));
$$;

-- 未対応の指摘の一覧。p_target：category / address / summary、p_check：チェックのコード
create function checks_list(p_target text default null, p_check text default null, p_area text default null,
                            p_severity text default null, p_q text default null,
                            p_offset int default 0, p_limit int default 50)
returns jsonb language sql stable as $$
select coalesce(jsonb_agg(x), '[]'::jsonb) from (
  select c.id, c.check_code, c.label, c.target, c.severity, c.detail, c.note_id, c.first_seen,
         s.id as spot_id, s.code, s.name, s.area_code, s.address, sc.name as category,
         (select left(n.body, 200) from spot_ai_notes n where n.id = c.note_id) as note_body
    from spot_checks c
    join spots s on s.id = c.spot_id
    left join spot_categories sc on sc.code = s.category
   where c.status = 'open' and s.data_status <> 'archived'
     and (p_target is null or c.target = p_target)
     and (p_check is null or c.check_code = p_check)
     and (p_area is null or s.area_code = p_area)
     and (p_severity is null or c.severity = p_severity)
     and (p_q is null or s.code = p_q or s.name ilike '%' || p_q || '%' or s.address ilike '%' || p_q || '%' or c.detail ilike '%' || p_q || '%')
   order by (c.severity = 'warn') desc, c.target, c.check_code, s.code, c.id
   offset p_offset limit p_limit
) x;
$$;

-- 人が「問題なし」として閉じる。同じ指摘は次の実行でも開き直さない（地点の履歴にも残す）
create function check_close(p_id bigint, p_user text, p_note text default null)
returns jsonb language plpgsql as $$
declare c record;
begin
  update spot_checks set status = 'dismissed', closed_by = coalesce(p_user, 'user'), closed_at = now(),
         close_note = nullif(btrim(coalesce(p_note, '')), '')
   where id = p_id and status = 'open'
  returning * into c;
  if not found then raise exception 'この指摘は見つからないか、すでに閉じています'; end if;
  insert into spot_history (spot_id, actor, event, detail)
  values (c.spot_id, coalesce(p_user, 'user'), 'check_dismissed',
          jsonb_build_object('check', c.check_code, 'detail', c.detail, 'note', c.close_note));
  return jsonb_build_object('id', c.id, 'spot_id', c.spot_id, 'status', 'dismissed');
end $$;
