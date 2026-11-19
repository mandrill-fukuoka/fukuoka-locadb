-- ============================================================
-- 260: 重要度の点数（順2。2026-11-19 利用者と合意。番号帯 260〜269）
--  検索の既定の並び順（おすすめ順）に地点の重要度を入れ、逆順の「穴場順」を足すための材料と点数。
--  - spot_signals：外部から取ったもとの数字（Wikipedia の月間閲覧数・Wikidata の言語版の数）。取得は tools/importance_import.py
--  - spot_importance：地点ごとの点数（0〜1）と内訳。refresh_spot_importance() が作り直す（sql/api/95_importance.sql）
--  - spot_facts の出どころに wikidata を足す（Wikidata は CC0。構造化データの事実は確認なしで取り込み、出どころ単位で取り消せる）
--  費用はかけない（AIは使わない）。点数は並べ替えにだけ使い、画面には「重要」とは書かない
-- ============================================================
create table if not exists spot_signals (
  spot_id    bigint not null references spots(id) on delete cascade,
  kind       text not null check (kind in ('wikipedia_views','wikidata_sitelinks')),
  value      real not null check (value >= 0),
  source_ref text,                                     -- 例：wikidata:Q123 / ja.wikipedia:福岡タワー
  fetched_at timestamptz not null default now(),
  primary key (spot_id, kind)
);

create table if not exists spot_importance (
  spot_id      bigint primary key references spots(id) on delete cascade,
  score        real not null check (score between 0 and 1),         -- 重要度（大きいほど代表的）
  hidden_score real not null default 0 check (hidden_score between 0 and 1),   -- 穴場度（材料は豊富だが知名度が低い）
  parts        jsonb not null,                                      -- 内訳（各0〜1）
  computed_at  timestamptz not null default now()
);
create index if not exists spot_importance_score_idx on spot_importance (score desc);

-- 事実の出どころに wikidata を足す
alter table spot_facts drop constraint if exists spot_facts_source_kind_check;
alter table spot_facts add constraint spot_facts_source_kind_check
  check (source_kind in ('wikipedia','web','osm','manual','wikidata'));
