-- ============================================================
-- 130: AIの出力は「下書き」として持ち、人が採用したものだけを公開する（タスクC・LAYA。2026-10-14 利用者と合意）
--  - spot_ai_notes：AI（LAYA＝ローカル/Ollama、claude＝Anthropic API）の下書き。出どころ・モデル・プロンプトの版を残す
--  - spots.summary は「人が書いた・採用した説明文」だけ。summary_by（誰が）が空の説明文は入れられない（制約）
--  - spot_tags.source：AIのタグの出どころ（laya / claude）。人のタグは origin='human'
--  - 別ラインの LAYA が spots.summary に直接書いていた説明文（人の確認記録なし）は、出どころ laya の下書きへ移す
--  書き込みは関数 ai_submit / ai_review（sql/api/50_ai.sql）から行う。表を直接 PATCH しない
-- ============================================================
create table if not exists spot_ai_notes (
  id             bigserial primary key,
  spot_id        bigint not null references spots(id) on delete cascade,
  source         text not null check (source in ('laya','claude')),
  kind           text not null default 'summary' check (kind in ('summary')),
  body           text not null check (length(body) between 1 and 2000),
  model          text,
  prompt_version text,
  status         text not null default 'draft' check (status in ('draft','accepted','rejected','superseded')),
  created_at     timestamptz not null default now(),
  reviewed_by    text,
  reviewed_at    timestamptz,
  review_note    text
);
create index if not exists spot_ai_notes_spot_idx on spot_ai_notes (spot_id, kind, status);
create index if not exists spot_ai_notes_draft_idx on spot_ai_notes (status, source) where status = 'draft';

alter table spots add column if not exists summary_by text;
alter table spots add column if not exists summary_at timestamptz;
alter table spot_tags add column if not exists source text check (source in ('laya','claude'));

-- 人が書いた説明文：手入力の地点（登録した人）と、確認画面で人が保存した記録のある地点
update spots s set summary_by = coalesce(
         (select h.actor from spot_history h where h.spot_id = s.id and h.event = 'reviewed' and h.actor <> 'pipeline'
           order by h.at desc limit 1),
         case when s.origin = 'manual' then
           (select h.actor from spot_history h where h.spot_id = s.id and h.event = 'created' order by h.at limit 1) end),
       summary_at = now()
 where s.summary is not null and s.summary_by is null
   and (s.origin = 'manual' or exists (select 1 from spot_history h where h.spot_id = s.id and h.event = 'reviewed'
                                                                        and h.actor <> 'pipeline'));

update spots set summary = null where summary is not null and btrim(summary) = '';   -- 空白だけの説明文は空に

-- それ以外の説明文（別ラインの LAYA が直接書いたもの）を下書きへ移す。移した件数と消した件数が違えば止める
do $$
declare n_moved int; n_cleared int;
begin
  insert into spot_ai_notes (spot_id, source, kind, body, model, prompt_version, status, created_at)
  select s.id, 'laya', 'summary', left(s.summary, 2000), null, 'before-2026-10-14', 'draft', s.updated_at
    from spots s where s.summary is not null and s.summary_by is null;
  get diagnostics n_moved = row_count;

  insert into spot_history (spot_id, actor, event, detail)
  select s.id, 'migration', 'summary_to_draft', jsonb_build_object('source', 'laya')
    from spots s where s.summary is not null and s.summary_by is null;

  update spots set summary = null where summary is not null and summary_by is null;
  get diagnostics n_cleared = row_count;

  raise notice '説明文を下書きへ移した件数: %（spots.summary を空にした件数: %）', n_moved, n_cleared;
  if n_moved <> n_cleared then
    raise exception '件数が合わないため止めました（移した % / 空にした %）', n_moved, n_cleared;
  end if;
end $$;

alter table spots drop constraint if exists spots_summary_by_check;
alter table spots add constraint spots_summary_by_check check (summary is null or summary_by is not null);
