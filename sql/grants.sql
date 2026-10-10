-- 権限（毎回実行。何度実行しても同じ結果）
--  画面（PHP）はサーバー側で service_role のキーだけを使う。anon / authenticated からは何も見せない。
--  全テーブルで RLS を有効にする（service_role は RLS の対象外なので画面は動く）。
do $$
declare t text; f record;
begin
  for t in select tablename from pg_tables where schemaname = 'public'
            and tablename not in ('spatial_ref_sys') loop
    execute format('alter table public.%I enable row level security', t);
  end loop;

  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on all tables in schema public from anon;
    revoke all on all sequences in schema public from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on all tables in schema public from authenticated;
    revoke all on all sequences in schema public from authenticated;
  end if;
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke all on schema legacy from anon';
  end if;

  -- このプロジェクトの関数（PostGIS などの拡張の関数は除く）
  for f in select p.oid::regprocedure as sig
             from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public'
              and not exists (select 1 from pg_depend d where d.objid = p.oid and d.deptype = 'e') loop
    execute format('revoke execute on function %s from public', f.sig);
    if exists (select 1 from pg_roles where rolname = 'anon') then
      execute format('revoke execute on function %s from anon', f.sig);
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
      execute format('revoke execute on function %s from authenticated', f.sig);
    end if;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
      execute format('grant execute on function %s to service_role', f.sig);
    end if;
  end loop;

  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant usage on schema public to service_role;
    grant select, insert, update, delete on all tables in schema public to service_role;
    grant usage, select on all sequences in schema public to service_role;
  end if;
end $$;
