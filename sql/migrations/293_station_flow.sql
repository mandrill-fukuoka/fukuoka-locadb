-- 駅の人の多さ（順6後半・人通り。2026-11-22。AIなし・費用ゼロ）
--  国土数値情報「駅別乗降客数データ（S12）」（国土交通省。CC BY 4.0）から、福岡市の範囲の駅の1日の乗降客数を使い、
--  地点ごとに「周り800m の駅の乗降客数（近いほど重く数える）」と最寄り駅を出す。駅の名前・位置・乗降客数だけを保存する
create table station_flow (
  group_code  text primary key,                      -- 駅のグループコード（300m以内の同名駅をまとめたもの）
  name        text not null,
  operators   text[] not null default '{}',
  pax         integer not null,                      -- 1日の乗降客数（人/日。事業者ごとの重複を除いた合計）
  fiscal_year integer not null,
  geom        geography(Point, 4326) not null,
  source      text not null,                         -- S12-23 など
  updated_at  timestamptz not null default now()
);
create index station_flow_geom_idx on station_flow using gist (geom);

create table spot_footfall (
  spot_id      bigint primary key references spots(id) on delete cascade,
  flow_800     integer not null,                     -- Σ 乗降客数 × (1 − 距離/800m)。駅が近くにないと 0
  station_m    integer,                              -- 最寄り駅までの距離（800m以内のときだけ）
  station_name text,
  station_pax  integer,
  updated_at   timestamptz not null default now()
);
