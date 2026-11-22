-- ============================================================
-- 290: 店の密度（順6の後半。2026-11-22。番号帯 290〜299）
--  Overture Maps の店舗データ（places。CDLA Permissive 2.0。OSM由来を含まない）を「密度」として使うための材料。
--  - place_points：福岡市の範囲の店・施設の点。名前・電話・URL などは取らない（種別と位置だけ）。月次の取り込みで作り直す
--  - spot_density：地点ごとの周りの数（半径150m・500m）。density_refresh() が作り直す（sql/api/98_density.sql）
--  地点にはしない（地点の追加候補にも入れない）。雰囲気の目安（vibe）の「周りの店の数」に使う。費用はかけない
-- ============================================================
create table if not exists place_points (
  id       bigserial primary key,
  gers_id  text not null,                              -- Overture の ID（次のリリースとの差分を見る材料）
  cls      text not null check (cls in ('bar','eat','retail','other')),
  cat      text,                                       -- Overture の basic_category
  conf     real,                                       -- Overture の信頼度（0〜1）
  geom     geography(Point, 4326) not null,
  release  text not null                               -- 例：2026-11-19.0
);
create index if not exists place_points_geom_idx on place_points using gist (geom);
create index if not exists place_points_cls_idx on place_points (cls);

create table if not exists spot_density (
  spot_id    bigint primary key references spots(id) on delete cascade,
  bar_150    integer not null default 0,               -- バー・居酒屋など夜の店
  eat_150    integer not null default 0,               -- 飲食店・カフェ
  shop_150   integer not null default 0,               -- 夜の店＋飲食＋物販（店の数）
  any_150    integer not null default 0,               -- すべての種別
  any_500    integer not null default 0,               -- 人通りの目安（半径500m内のすべての種別）
  updated_at timestamptz not null default now()
);
