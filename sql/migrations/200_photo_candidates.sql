-- ============================================================
-- 200: 写真候補（修正提案の写真。2026-10-22 利用者と合意）
--  Wikimedia Commons の位置情報つき写真を「候補」として集め、人が採否を決める（AIは使わない）。
--  - 候補（spot_photo_candidates）は公開しない。採用（status='adopted'）のとき spot_photos に入れ、地点ページに出す
--  - 利用条件：CC0・パブリックドメイン・CC BY・CC BY-SA だけ。作者・ライセンス名・ライセンスへのリンク・出典ページを必ず持つ
--    （Commons の再利用の決まり：作者表示、ライセンス全文へのリンク。NC・ND・fair use は取らない）
--  - 画像そのものは保存しない（Commons の縮小画像のURLを参照する）。肖像権・商標などは別途、人が見て判断する
-- ============================================================
create table if not exists spot_photo_candidates (
  id           bigserial primary key,
  spot_id      bigint not null references spots(id) on delete cascade,
  source       text not null default 'commons' check (source in ('commons')),
  file_title   text not null,                           -- 例：File:Kushida Shrine.jpg
  page_url     text not null check (page_url ~ '^https://'),    -- Commons のファイル説明ページ（出典）
  image_url    text not null check (image_url ~ '^https://'),   -- 縮小画像のURL
  author       text,
  license      text not null,                           -- 例：CC BY-SA 4.0
  license_url  text,
  description  text,
  shot_at      text,
  dist_m       real,                                    -- 地点から写真の位置までの距離
  name_match   boolean not null default false,          -- ファイル名・説明に地点名が含まれる
  status       text not null default 'candidate' check (status in ('candidate','adopted','rejected')),
  decided_by   text,
  decided_at   timestamptz,
  fetched_at   timestamptz not null default now(),
  unique (spot_id, file_title)
);
create index if not exists spot_photo_cand_status_idx on spot_photo_candidates (status, spot_id);

-- 探した地点の記録（候補が1件もなかった地点を毎回探し直さないため）
create table if not exists spot_photo_search (
  spot_id     bigint primary key references spots(id) on delete cascade,
  searched_at timestamptz not null default now(),
  found       int not null default 0
);

-- 採用した写真の出典（spot_photos は手入力用の表。出典の列を足す）
alter table spot_photos add column if not exists author      text;
alter table spot_photos add column if not exists title       text;
alter table spot_photos add column if not exists source_url  text;
alter table spot_photos add column if not exists license_url text;
