-- ============================================================
-- 203: 追加候補（DBにない場所の候補。2026-10-22 利用者と合意）
--  日本語Wikipediaの座標つき記事と、Wikimedia Commons の写真が集まる場所から、「DBにまだない場所」を候補として集める。
--  - 候補は公開しない。確認画面の「追加候補」で人が名前・種別を確かめて「採用」したときだけ、手入力の地点（origin=manual）になる
--  - 記事の本文は保存しない（名前・座標・記事へのリンクだけ。Wikipedia は CC BY-SA のため、出典として記事のURLを残す）
--  - 撮影の可否・評価は書かない。種別の初期値は題名の語から決めた案（人が直せる）
-- ============================================================
create table if not exists spot_additions (
  id            bigserial primary key,
  source        text not null check (source in ('wikipedia','commons')),
  source_key    text not null unique,                    -- wikipedia:<pageid> / commons:<緯度,経度（約100m四方）>
  name          text not null,                           -- Wikipedia：記事名（（）の補足は除く）／Commons：（名称未設定）→人が付ける
  lat           double precision not null,
  lon           double precision not null,
  area_code     text references areas(code),
  suggest_cat   text references spot_categories(code),   -- 題名から決めた種別の案（なければ null＝人が選ぶ）
  source_url    text not null check (source_url ~ '^https://'),
  files         int,                                     -- Commons：この場所に集まった写真の枚数
  samples       jsonb not null default '[]'::jsonb,      -- Commons：写真の題名とURLの例
  near_name     text,                                    -- いちばん近い既存の地点（重複でないかの目安）
  near_m        real,
  status        text not null default 'candidate' check (status in ('candidate','adopted','rejected')),
  spot_id       bigint references spots(id) on delete set null,   -- 採用してできた地点
  decided_by    text,
  decided_at    timestamptz,
  fetched_at    timestamptz not null default now()
);
create index if not exists spot_additions_status_idx on spot_additions (status, source, id);
