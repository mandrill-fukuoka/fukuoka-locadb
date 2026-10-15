-- AIの書き込み口と、人による採用・却下（DB変更130。tools/migrate.py が毎回作り直す）
--  AI（LAYA・claude）は表を直接書かず、ai_submit だけを呼ぶ。公開の値（spots.summary）は人が ai_review で採用したときだけ変わる
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in ('ai_submit','ai_review','ai_drafts')
  loop execute format('drop function %s', f.sig); end loop;
end $$;

-- AIの出力を受け取る。p_items：[{"spot_id": 123, "summary": "…", "tags": [{"code": "nostalgic", "score": 0.8}]}, …]（最大200件）
--  - 説明文 → spot_ai_notes の下書き（同じ地点・同じ出どころの前の下書きは superseded）。1〜800字
--  - タグ  → spot_tags（origin='ai', source=出どころ）。tags 表の語彙だけ。人が付けたタグ（origin='human'）は変えない。
--            その地点の同じ出どころのAIタグは、今回の内容で置き換える（"tags" を省いた地点はタグに触れない）
--  - 対象外・存在しない地点、語彙にないタグ、長すぎる説明文は弾いて rejected に理由を返す（他の項目は受け付ける）
create function ai_submit(p_source text, p_items jsonb, p_model text default null, p_prompt_version text default null)
returns jsonb language plpgsql as $$
declare
  it jsonb; v_id bigint; v_sum text; t jsonb; v_code text; v_score real;
  n_sum int := 0; n_tag int := 0; rej jsonb := '[]'::jsonb;
begin
  if p_source not in ('laya', 'claude') then raise exception 'p_source は laya か claude'; end if;
  if jsonb_typeof(p_items) <> 'array' then raise exception 'p_items は配列'; end if;
  if jsonb_array_length(p_items) > 200 then raise exception '1回に送れるのは200件まで（%件）', jsonb_array_length(p_items); end if;

  for it in select * from jsonb_array_elements(p_items) loop
    v_id := nullif(it ->> 'spot_id', '')::bigint;
    if v_id is null or not exists (select 1 from spots where id = v_id and data_status <> 'archived') then
      rej := rej || jsonb_build_object('spot_id', it -> 'spot_id', 'reason', '地点がない、または対象外');
      continue;
    end if;

    v_sum := nullif(btrim(coalesce(it ->> 'summary', '')), '');
    if v_sum is not null then
      if length(v_sum) > 800 then
        rej := rej || jsonb_build_object('spot_id', v_id, 'reason', '説明文が800字を超えている（' || length(v_sum) || '字）');
      else
        update spot_ai_notes set status = 'superseded'
         where spot_id = v_id and source = p_source and kind = 'summary' and status = 'draft';
        insert into spot_ai_notes (spot_id, source, kind, body, model, prompt_version)
        values (v_id, p_source, 'summary', v_sum, left(p_model, 100), left(p_prompt_version, 100));
        n_sum := n_sum + 1;
      end if;
    end if;

    if it ? 'tags' and jsonb_typeof(it -> 'tags') = 'array' then
      delete from spot_tags where spot_id = v_id and origin = 'ai' and source = p_source;
      for t in select * from jsonb_array_elements(it -> 'tags') loop
        v_code := t ->> 'code';
        v_score := least(1, greatest(0, coalesce(nullif(t ->> 'score', '')::real, 0.5)));
        if not exists (select 1 from tags where code = v_code) then
          rej := rej || jsonb_build_object('spot_id', v_id, 'reason', '語彙にないタグ：' || coalesce(v_code, '(空)'));
          continue;
        end if;
        insert into spot_tags (spot_id, tag_code, origin, score, source)
        values (v_id, v_code, 'ai', v_score, p_source)
        on conflict (spot_id, tag_code) do update set score = excluded.score, source = excluded.source
          where spot_tags.origin = 'ai';                       -- 人のタグはそのまま
        n_tag := n_tag + 1;
      end loop;
    end if;
  end loop;
  return jsonb_build_object('summaries', n_sum, 'tags', n_tag, 'rejected', rej);
end $$;

-- 下書きの採用・却下（ログインした人）。p_action：accept（そのまま／p_text で直して採用）/ reject
--  採用すると spots.summary に入り、summary_by＝採用した人。その地点の他の説明文の下書きは superseded
create function ai_review(p_note_id bigint, p_action text, p_user text, p_text text default null, p_note text default null)
returns jsonb language plpgsql as $$
declare n record; v_text text;
begin
  select * into n from spot_ai_notes where id = p_note_id for update;
  if not found then raise exception '下書きが見つかりません'; end if;
  if n.status <> 'draft' then raise exception 'この下書きはすでに処理済みです（%）', n.status; end if;
  if p_action = 'accept' then
    v_text := coalesce(nullif(btrim(coalesce(p_text, '')), ''), n.body);
    if length(v_text) > 800 then raise exception '説明文は800字までです'; end if;
    update spots set summary = v_text, summary_by = coalesce(p_user, 'user'), summary_at = now() where id = n.spot_id;
    update spot_ai_notes set status = 'accepted', reviewed_by = coalesce(p_user, 'user'), reviewed_at = now(),
           review_note = case when v_text <> n.body then '直して採用' else p_note end
     where id = p_note_id;
    update spot_ai_notes set status = 'superseded'
     where spot_id = n.spot_id and kind = n.kind and status = 'draft' and id <> p_note_id;
    insert into spot_history (spot_id, actor, event, detail)
    values (n.spot_id, coalesce(p_user, 'user'), 'summary_accepted',
            jsonb_build_object('note', p_note_id, 'source', n.source, 'edited', v_text <> n.body));
  elsif p_action = 'reject' then
    update spot_ai_notes set status = 'rejected', reviewed_by = coalesce(p_user, 'user'), reviewed_at = now(),
           review_note = p_note where id = p_note_id;
    insert into spot_history (spot_id, actor, event, detail)
    values (n.spot_id, coalesce(p_user, 'user'), 'summary_rejected', jsonb_build_object('note', p_note_id, 'source', n.source));
  else
    raise exception 'p_action は accept か reject';
  end if;
  return jsonb_build_object('spot_id', n.spot_id, 'status', case when p_action = 'accept' then 'accepted' else 'rejected' end);
end $$;

-- 採用待ちの下書き（確認画面の「説明文の下書き」タブ）
--  自動チェック（DB変更170）の未対応の指摘を checks に付け、「要確認」の指摘がある下書きを先に並べる。
--  p_flagged：true＝指摘のある下書きだけ、false＝指摘のないものだけ、null＝すべて
create function ai_drafts(p_area text default null, p_q text default null, p_source text default null,
                          p_offset int default 0, p_limit int default 30, p_flagged boolean default null)
returns jsonb language sql stable as $$
select coalesce(jsonb_agg(x), '[]'::jsonb) from (
  select n.id, n.source, n.body, n.model, n.prompt_version, n.created_at,
         s.id as spot_id, s.code, s.name, s.area_code, s.address, sc.name as category, s.summary as current_summary,
         s.confidence, s.status, ck.checks
    from spot_ai_notes n
    join spots s on s.id = n.spot_id
    left join spot_categories sc on sc.code = s.category
    left join lateral (
      select jsonb_agg(jsonb_build_object('id', c.id, 'label', c.label, 'severity', c.severity, 'detail', c.detail)
                       order by (c.severity = 'warn') desc, c.check_code) as checks,
             bool_or(c.severity = 'warn') as has_warn
        from spot_checks c where c.note_id = n.id and c.status = 'open') ck on true
   where n.status = 'draft' and s.data_status <> 'archived'
     and (p_area is null or s.area_code = p_area)
     and (p_source is null or n.source = p_source)
     and (p_q is null or s.name ilike '%' || p_q || '%' or s.address ilike '%' || p_q || '%' or n.body ilike '%' || p_q || '%')
     and (p_flagged is null or (ck.checks is not null) = p_flagged)
   order by coalesce(ck.has_warn, false) desc, (ck.checks is not null) desc, s.code, n.created_at desc
   offset p_offset limit p_limit
) x;
$$;
