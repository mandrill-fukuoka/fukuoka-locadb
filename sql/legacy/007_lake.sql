-- ============================================================
-- 007: 景観「池・湖」を追加（池・湖・ため池・お堀・ダム湖）
-- Supabase の SQL Editor に全文を貼り付けて Run（何度実行しても安全）
-- ※ 006_water_split.sql を先に実行してください
-- ============================================================

insert into landscape_groups (code, name) values ('lake', '池・湖')
on conflict (code) do update set name = excluded.name;
update landscape_groups set name = '水辺（川・海・砂浜・池のどれでも）' where code = 'water';

insert into spot_categories (code, name, landscape_group) values ('pond', '池・湖・ため池・お堀', 'lake')
on conflict (code) do update set name = excluded.name, landscape_group = excluded.landscape_group;

-- まとめの「水辺」に池・湖も含める
create or replace function grp_match(p_want text, p_actual text) returns boolean
language sql immutable as $$
  select p_want = p_actual or (p_want = 'water' and p_actual in ('river', 'sea', 'beach', 'lake', 'water'));
$$;

-- 登録済みの地点で、名前が池・湖・堀のものを振り分け（公園の中の池などは次回の収集で追加されます）
update spots set category = 'pond'
 where category in ('park', 'river', 'coast')
   and name ~ '(池|湖|ため池|溜池|お堀|堀)$'
   and name !~ '(公園|緑地|庭園)$';

do $$
begin
  revoke execute on function grp_match(text,text) from public;
  if exists (select 1 from pg_roles where rolname = 'anon') then revoke execute on function grp_match(text,text) from anon; end if;
  if exists (select 1 from pg_roles where rolname = 'service_role') then grant execute on function grp_match(text,text) to service_role; end if;
end $$;

-- 結果確認
select lg.name as 景観, count(s.id) as 地点数
  from landscape_groups lg
  left join spot_categories sc on sc.landscape_group = lg.code
  left join spots s on s.category = sc.code and s.status <> 'archived'
 where lg.code in ('river', 'sea', 'beach', 'lake')
 group by lg.code, lg.name order by lg.code;
