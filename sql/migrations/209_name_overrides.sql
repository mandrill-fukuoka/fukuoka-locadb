-- ============================================================
-- 209: 日本語名の上書き（2026-10-25 利用者と合意）
--  OSM に name:ja が無い（または英語・ローマ字の name だけの）地点について、人が直した日本語名を保存する。
--  取り込み（tools/name_ja_import.py）が書き、毎週の自動収集（pipeline）は、OSM に name:ja が無いときだけこの名前を使う。
--  OSM 側に name:ja が付けば、そちらが優先（この表は使われなくなる）。
-- ============================================================
create table if not exists feature_name_overrides (
  feature_key text primary key,                 -- n123 / w123 / r123（features.key）
  name_ja     text not null check (btrim(name_ja) <> ''),
  source      text,                             -- 取り込んだファイル名
  created_at  timestamptz not null default now()
);
