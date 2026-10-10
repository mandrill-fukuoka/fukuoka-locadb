-- 福岡ロケーションデータベース スキーマ（PostgreSQL 15+ / PostGIS 3+、Supabase対応）
create extension if not exists postgis;
create extension if not exists pg_trgm;

-- ===== マスタ =====
create table if not exists areas (
  code        text primary key,            -- 例: fuk-chuo
  name        text not null,               -- 中央区
  stage       smallint not null default 1, -- 1:福岡市 2:都市圏 3:県内
  geom        geography(MultiPolygon)      -- 区域（任意）
);

-- 景観グループ（切り替わり判定の単位）
create table if not exists landscape_groups (
  code  text primary key,   -- urban_new / old_town / water / port_industrial ...
  name  text not null
);

-- 地点種別（企画書4章の分類）
create table if not exists spot_categories (
  code            text primary key,
  name            text not null,
  landscape_group text not null references landscape_groups(code)
);

-- 検索タグ（雰囲気・時間帯・時代感など）
create table if not exists tags (
  code   text primary key,      -- lonely / night_ok / showa ...
  name   text not null,         -- 寂しい
  facet  text not null          -- mood / time / era / density / feature
);

-- ===== 収集ステージング =====
create table if not exists raw_candidates (
  id           bigserial primary key,
  source       text not null,           -- osm / opendata / ffc / manual
  source_ref   text not null,           -- 例: osm:way/12345
  name         text,
  geom         geography(Point) not null,
  raw          jsonb not null,
  category_hint text,
  fetched_at   timestamptz not null default now(),
  spot_id      bigint,                  -- 統合後の地点
  unique (source, source_ref)
);
create index if not exists raw_candidates_geom_idx on raw_candidates using gist (geom);

-- ===== 地点 =====
create table if not exists spots (
  id            bigserial primary key,
  code          text unique,                         -- FUK-CHU-S0001
  name          text not null,
  alt_names     text[] default '{}',
  address       text,
  area_code     text references areas(code),
  geom          geography(Point) not null,
  category      text references spot_categories(code),
  era_feel      text check (era_feel in ('showa','heisei','reiwa','mixed','historic')),
  shoot_scope   text check (shoot_scope in ('exterior_public','exterior_offsite','interior_permit','unknown')) default 'unknown',
  owner_type    text check (owner_type in ('public','private','mixed','unknown')) default 'unknown',
  ffc_registered boolean default false,
  ffc_ref       text,
  summary       text,
  status        text not null default 'candidate'
                check (status in ('candidate','ai_tagged','scouted','verified','archived')),
  confidence    text not null default 'unverified'
                check (confidence in ('unverified','ai_estimated','desk_checked','field_checked')),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists spots_geom_idx on spots using gist (geom);
create index if not exists spots_name_trgm on spots using gin (name gin_trgm_ops);

create table if not exists spot_tags (
  spot_id   bigint references spots(id) on delete cascade,
  tag_code  text references tags(code),
  origin    text not null default 'ai' check (origin in ('ai','human')),
  score     real default 1.0,     -- AI確信度 0-1
  primary key (spot_id, tag_code)
);

-- 撮影実務
create table if not exists spot_practicals (
  spot_id          bigint primary key references spots(id) on delete cascade,
  permit_required  text,     -- 道路使用許可、施設管理者許可 など
  permit_contact   text,     -- 公式窓口のみ
  restrictions     text,
  past_productions jsonb default '[]',  -- [{title, year, source}]
  notes            text
);

create table if not exists spot_photos (
  id         bigserial primary key,
  spot_id    bigint references spots(id) on delete cascade,
  url        text not null,      -- 自社ストレージのURL（第三者画像は保存しない）
  shot_at    date,
  shot_by    text,
  heading    smallint,           -- 撮影方角（度）
  time_of_day text,
  license    text default 'own'
);

-- ===== クラスター =====
create table if not exists clusters (
  id                bigserial primary key,
  code              text unique,          -- FUK-CHU-C001
  name              text,
  area_code         text references areas(code),
  transition_score  real,                 -- 景観の切り替わり速度スコア
  transition_label  text,                 -- 繁華街 → 細街路 → 川沿い
  total_minutes     real,
  travel_mode       text check (travel_mode in ('walk','car','mixed')),
  suitable_scenes   text[],
  origin            text not null default 'auto' check (origin in ('auto','human')),
  status            text not null default 'candidate' check (status in ('candidate','reviewed','verified','archived')),
  created_at        timestamptz not null default now()
);

create table if not exists cluster_spots (
  cluster_id      bigint references clusters(id) on delete cascade,
  seq             smallint not null,
  spot_id         bigint references spots(id) on delete cascade,
  minutes_from_prev real,
  meters_from_prev  real,
  primary key (cluster_id, seq)
);

-- ===== 変化リスク =====
create table if not exists change_risks (
  id          bigserial primary key,
  spot_id     bigint references spots(id) on delete cascade,
  risk_type   text not null check (risk_type in ('redevelopment','demolition','closure','use_change','none')),
  evidence    text,
  source_url  text,
  verified    boolean not null default false,   -- false = 変化リスク要確認
  detected_at timestamptz not null default now(),
  review_by   date
);

-- ===== 出典・監視 =====
create table if not exists sources (
  id          bigserial primary key,
  spot_id     bigint references spots(id) on delete cascade,
  kind        text not null,       -- osm / opendata / official / news / field
  title       text,
  url         text,
  retrieved_at timestamptz not null default now(),
  note        text
);

create table if not exists watch_pages (
  id          bigserial primary key,
  url         text unique not null,
  label       text,
  last_hash   text,
  last_checked timestamptz
);

create table if not exists job_runs (
  id         bigserial primary key,
  job        text not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  stats      jsonb,
  error      text
);

-- updated_at 自動更新
create or replace function touch_updated_at() returns trigger as $$
begin new.updated_at = now(); return new; end $$ language plpgsql;
drop trigger if exists spots_touch on spots;
create trigger spots_touch before update on spots for each row execute function touch_updated_at();
