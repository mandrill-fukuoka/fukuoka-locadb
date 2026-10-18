-- 事実カード（タスクH、DB変更180。tools/migrate.py が毎回作り直す）
--  書くのは facts_import（人が承認した事実の取り込み）と fact_revoke（取り消し）だけ。画面は facts_for_spot で読む
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in ('facts_for_spot','facts_import','fact_revoke','facts_counts')
  loop execute format('drop function %s', f.sig); end loop;
end $$;

-- 地点の承認済みの事実（公開用。原文 quote は含めない）
create function facts_for_spot(p_id bigint) returns jsonb language sql stable as $$
select coalesce(jsonb_agg(jsonb_build_object(
         'id', f.id, 'item', f.item, 'value', f.value, 'note', f.note, 'as_of', f.as_of,
         'source_kind', f.source_kind, 'source_url', f.source_url, 'source_title', f.source_title, 'license', f.license)
       order by array_position(array['管理者・所有者','創設・開設年','規模','現況','指定・登録','人口'], f.item), f.id), '[]'::jsonb)
  from spot_facts f
 where f.spot_id = p_id and f.status = 'approved';
$$;

-- 項目ごとの承認済みの件数
create function facts_counts() returns jsonb language sql stable as $$
select jsonb_build_object(
  'items', coalesce((select jsonb_agg(x order by x.item) from (
      select f.item, count(*) as n, count(distinct f.spot_id) as spots
        from spot_facts f join spots s on s.id = f.spot_id
       where f.status = 'approved' and s.data_status <> 'archived' group by f.item) x), '[]'::jsonb),
  'spots', (select count(distinct f.spot_id) from spot_facts f join spots s on s.id = f.spot_id
             where f.status = 'approved' and s.data_status <> 'archived'));
$$;

-- 人が承認した事実を取り込む。p_items：[{"spot_id":1,"item":"規模","value":"351戸","note":"戸数","as_of":null,
--   "source_kind":"web","source_url":"https://…","source_title":"…","license":null,"quote":"…"}, …]（最大200件）
--  - 対象外・存在しない地点、項目の語彙外、空の値、出典のないもの、Wikipedia で記事名・ライセンスのないものは弾いて rejected に理由を返す
--  - 同じ（地点・項目・値・出典）が承認済みなら skipped。取り込みの承認者は p_user（人）。AIは使わない
create function facts_import(p_items jsonb, p_user text) returns jsonb language plpgsql as $$
declare
  it jsonb; v_id bigint; v_item text; v_val text; v_kind text; v_url text; v_fid bigint;
  n_add int := 0; n_skip int := 0; rej jsonb := '[]'::jsonb;
begin
  if nullif(btrim(coalesce(p_user, '')), '') is null then raise exception 'p_user（承認した人）が必要'; end if;
  if jsonb_typeof(p_items) <> 'array' then raise exception 'p_items は配列'; end if;
  if jsonb_array_length(p_items) > 200 then raise exception '1回に取り込めるのは200件まで（%件）', jsonb_array_length(p_items); end if;

  for it in select * from jsonb_array_elements(p_items) loop
    v_id   := nullif(it ->> 'spot_id', '')::bigint;
    v_item := it ->> 'item';
    v_val  := nullif(btrim(coalesce(it ->> 'value', '')), '');
    v_kind := it ->> 'source_kind';
    v_url  := nullif(btrim(coalesce(it ->> 'source_url', '')), '');
    if v_id is null or not exists (select 1 from spots where id = v_id and data_status <> 'archived') then
      rej := rej || jsonb_build_object('spot_id', it -> 'spot_id', 'item', v_item, 'reason', '地点がない、または対象外'); continue;
    end if;
    if v_item is null or v_item not in ('管理者・所有者','創設・開設年','規模','現況','指定・登録','人口') then
      rej := rej || jsonb_build_object('spot_id', v_id, 'item', v_item, 'reason', '項目が語彙にない'); continue;
    end if;
    if v_val is null or length(v_val) > 200 then
      rej := rej || jsonb_build_object('spot_id', v_id, 'item', v_item, 'reason', '値が空、または200字を超えている'); continue;
    end if;
    if v_kind is null or v_kind not in ('wikipedia','web','osm','manual') then
      rej := rej || jsonb_build_object('spot_id', v_id, 'item', v_item, 'reason', '取得元の種類が不正'); continue;
    end if;
    if v_kind <> 'manual' and (v_url is null or v_url !~ '^https://') then
      rej := rej || jsonb_build_object('spot_id', v_id, 'item', v_item, 'reason', '出典URL（https）がない'); continue;
    end if;
    if v_kind = 'wikipedia' and (nullif(it ->> 'source_title', '') is null or nullif(it ->> 'license', '') is null) then
      rej := rej || jsonb_build_object('spot_id', v_id, 'item', v_item, 'reason', 'Wikipedia は記事名とライセンスが必要'); continue;
    end if;
    if exists (select 1 from spot_facts where spot_id = v_id and item = v_item and value = v_val
                  and coalesce(source_url, '') = coalesce(v_url, '') and status = 'approved') then
      n_skip := n_skip + 1; continue;
    end if;
    insert into spot_facts (spot_id, item, value, note, as_of, source_kind, source_url, source_title, license, quote, approved_by)
    values (v_id, v_item, v_val, nullif(btrim(coalesce(it ->> 'note', '')), ''), nullif(btrim(coalesce(it ->> 'as_of', '')), ''),
            v_kind, v_url, nullif(it ->> 'source_title', ''), nullif(it ->> 'license', ''), nullif(it ->> 'quote', ''), p_user)
    returning id into v_fid;
    insert into spot_history (spot_id, actor, event, detail)
    values (v_id, p_user, 'fact_added', jsonb_build_object('fact_id', v_fid, 'item', v_item, 'value', v_val, 'source_url', v_url));
    n_add := n_add + 1;
  end loop;
  return jsonb_build_object('added', n_add, 'skipped', n_skip, 'rejected', rej);
end $$;

-- 事実を取り消す（行は残す）
create function fact_revoke(p_id bigint, p_user text, p_note text default null) returns jsonb language plpgsql as $$
declare f record;
begin
  update spot_facts set status = 'revoked', revoked_by = coalesce(nullif(btrim(coalesce(p_user, '')), ''), 'user'),
         revoked_at = now(), revoke_note = nullif(btrim(coalesce(p_note, '')), '')
   where id = p_id and status = 'approved'
  returning * into f;
  if not found then raise exception 'この事実は見つからないか、すでに取り消しています'; end if;
  insert into spot_history (spot_id, actor, event, detail)
  values (f.spot_id, coalesce(f.revoked_by, 'user'), 'fact_revoked',
          jsonb_build_object('fact_id', f.id, 'item', f.item, 'value', f.value, 'note', f.revoke_note));
  return jsonb_build_object('id', f.id, 'spot_id', f.spot_id, 'status', 'revoked');
end $$;
