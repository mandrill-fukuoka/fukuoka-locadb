-- 昼の人の多さ（順7・経済センサス。2026-11-26。AIなし・費用ゼロ）
--  政府統計の総合窓口（e-Stat）の「平成28年経済センサス-活動調査 地域メッシュ統計（500m）」から、福岡市の範囲の
--  「全産業の従業者数」だけを使い、地点ごとに「周り800m の従業者数（近いほど重く数える）」を出す。メッシュの位置と従業者数だけを保存する
create table workplace_mesh (
  mesh_code   text primary key,                      -- 500m メッシュコード（9桁）
  workers     integer not null,                      -- 全産業（公務を除く）の従業者数
  survey_year integer not null,
  geom        geography(Point, 4326) not null,       -- メッシュの中心
  source      text not null,                         -- e-Stat の表の番号
  updated_at  timestamptz not null default now()
);
create index workplace_mesh_geom_idx on workplace_mesh using gist (geom);

create table spot_daytime (
  spot_id      bigint primary key references spots(id) on delete cascade,
  workers_800  integer not null,                     -- Σ 従業者数 × (1 − 距離/800m)。メッシュが近くにないと 0
  updated_at   timestamptz not null default now()
);
