-- ============================================================
-- 190: 空間特徴（タスクI。2026-10-18 利用者と合意）
--  OSMの建物・道路・水辺から、地点ごとに計算した「まわりの空間の特徴」（locadb/spatial.py。自動収集の一工程）。
--  AIは使わない。事実と推定を区別して持つ（検索・雰囲気タグの材料）。計算方法と閾値は仕様書10章タスクI。
--  - building_cover_100  半径100m内の建物の面積率（0〜1）。事実（OSMに建物が描かれている範囲で）
--  - building_count_100  半径100m内の建物の棟数。事実（同上）
--  - open_ratio          開放性（0〜1）：16方向に150mまで見て、最初に建物に当たる距離の平均 ÷ 150。推定
--  - sight_max_m         見通し：16方向のうち最も遠くまで建物に当たらない距離（m、最大150）。推定
--  - water_dist_m        最寄りの水辺（川・海岸・池・砂浜）までの距離（m）。事実
--  - water_kind          その水辺の種類 river / coast / pond / beach
--  - road_class          最寄りの道（30m以内）の OSM highway 値。なければ空
--  - road_width_m        その道の幅の目安（m）。road_width_src が osm_width なら実測の記載、それ以外は推定
--  - road_width_src      osm_width（OSMの width タグ）/ lanes（車線数×3m）/ class（道の種別からの目安）
--  - confidence          low = 周囲300m以内の建物が3棟未満（空き地か、OSMに建物が描かれていない地域か区別できない）
--  - spatial_version     計算方法の版。版が変わったら取り直す
-- ============================================================
create table if not exists spot_spatial (
  spot_id            bigint primary key references spots(id) on delete cascade,
  spatial_version    text not null,
  computed_at        timestamptz not null default now(),
  building_cover_100 real,
  building_count_100 integer,
  open_ratio         real,
  sight_max_m        real,
  water_dist_m       real,
  water_kind         text check (water_kind in ('river','coast','pond','beach')),
  road_class         text,
  road_width_m       real,
  road_width_src     text check (road_width_src in ('osm_width','lanes','class')),
  confidence         text not null check (confidence in ('ok','low'))
);
create index if not exists spot_spatial_version_idx on spot_spatial (spatial_version);
