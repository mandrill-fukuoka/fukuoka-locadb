-- ============================================================
-- 204: ドローンの目安（人口集中地区）と潮位（修正提案3 層1。2026-10-22 利用者と合意）
--  did_areas：国土数値情報 人口集中地区 A16（平成27年・福岡県）。商用利用可（出典の表示と加工の明示が必要）。tools/layer1_import.py did で入れる
--  tide_daily：気象庁 潮位表 博多（観測所 QF）の日ごとの満潮・干潮の予報。出典「気象庁ホームページ」＋加工の明示。tools/layer1_import.py tide で入れる
--  どちらも「目安」。ドローンの飛行の可否は判断しない（人口集中地区・空港周辺は許可や届出が必要になる場合がある、という注意と国土交通省へのリンクまで）
-- ============================================================
create table if not exists did_areas (
  id        serial primary key,
  name      text not null,                 -- 市区町村名（A16_002以降の名称）
  pop       int,                           -- 平成27年の人口
  geom      geography(MultiPolygon, 4326) not null,
  source    text not null default '国土数値情報 人口集中地区データ（平成27年）'
);
create index if not exists did_areas_geom_idx on did_areas using gist (geom);

create table if not exists tide_daily (
  day       date primary key,
  station   text not null default 'QF',    -- 博多
  highs     jsonb not null default '[]'::jsonb,   -- [{"t":"08:29","cm":145}, …]
  lows      jsonb not null default '[]'::jsonb,
  fetched_at timestamptz not null default now()
);
