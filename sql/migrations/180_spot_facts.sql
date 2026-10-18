-- ============================================================
-- 180: 事実カード（タスクH。2026-10-18 利用者と合意）
--  人が承認した事実を「項目: 値（出典）」の形で持つ。説明文の下書き（spot_ai_notes）とは別の表。
--  - 承認済み（status='approved'）だけを画面に出す。ログインなしの画面にも出す（利用者の判断 2026-10-18）
--  - 出典のない事実は承認できない（手入力 manual を除く）。Wikipedia は記事名とライセンスの表示が必要
--  - 取り消しは行を消さず status='revoked'（履歴は spot_history にも残す）
--  - 項目は固定（語彙を増やすときは、この制約を直す変更を新しい番号で入れる）
-- ============================================================
create table if not exists spot_facts (
  id           bigserial primary key,
  spot_id      bigint not null references spots(id) on delete cascade,
  item         text not null check (item in ('管理者・所有者','創設・開設年','規模','現況','指定・登録','人口')),
  value        text not null check (length(btrim(value)) between 1 and 200),
  note         text,                                   -- 細目（戸数・面積・標高・創立／開設など）
  as_of        text,                                   -- 時点（人口など。「2022年9月末現在」）
  source_kind  text not null check (source_kind in ('wikipedia','web','osm','manual')),
  source_url   text,
  source_title text,                                   -- 記事名・ページ名
  license      text,                                   -- 例：CC BY-SA 4.0（Wikipedia）
  quote        text,                                   -- 根拠にした原文（確認用。画面には出さない）
  status       text not null default 'approved' check (status in ('approved','revoked')),
  approved_by  text not null,
  approved_at  timestamptz not null default now(),
  revoked_by   text,
  revoked_at   timestamptz,
  revoke_note  text,
  created_at   timestamptz not null default now(),
  constraint spot_facts_source_chk
    check (source_kind = 'manual' or coalesce(source_url ~ '^https://', false)),   -- NULL でもすり抜けない
  constraint spot_facts_wikipedia_chk
    check (source_kind <> 'wikipedia' or (source_title is not null and license is not null))
);
create unique index if not exists spot_facts_uniq on spot_facts (spot_id, item, value, coalesce(source_url, '')) where status = 'approved';
create index if not exists spot_facts_spot_idx on spot_facts (spot_id) where status = 'approved';
create index if not exists spot_facts_item_idx on spot_facts (item, spot_id) where status = 'approved';
