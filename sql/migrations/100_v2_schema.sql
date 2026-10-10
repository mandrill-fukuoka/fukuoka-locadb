-- ============================================================
-- 100: データベースの作り直し（v2）
--  旧方式の表は消さずに legacy スキーマへ移す（あとから参照・取り込みできる）。
--  新しい表は「OSMの要素（features）」と「画面に出す地点（spots）」を分け、
--  変化はすべて spot_history に記録する。現地確認の状態とデータの状態を分ける。
--  tools/migrate.py が1回だけ実行する（実行記録は schema_migrations）。
-- ============================================================
create extension if not exists postgis;
create extension if not exists pg_trgm;
create schema if not exists legacy;

-- ---------- 旧方式の表・関数を legacy へ ----------
do $$
declare t text; f record;
begin
  if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = 'raw_candidates') then
    drop view if exists public.v_review_queue, public.v_clusters, public.v_risks_to_check;
    for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
              where n.nspname = 'public' and p.proname in (
                'seq_match','search_clusters','review_queue','review_spot','review_risk','ping','grp_match',
                'build_routes','upsert_route','record_route_search','recommended_flows','popular_sequences',
                'archive_flow','search_spots','spots_nearby')
    loop
      execute format('drop function %s', f.sig);
    end loop;
    foreach t in array array['cluster_spots','clusters','spot_tags','spot_practicals','spot_photos','change_risks',
                             'sources','raw_candidates','spots','spot_categories','landscape_groups','tags','areas',
                             'watch_pages','job_runs','search_logs'] loop
      if exists (select 1 from information_schema.tables where table_schema = 'public' and table_name = t) then
        execute format('alter table public.%I set schema legacy', t);
      end if;
    end loop;
  end if;
end $$;

-- ---------- マスタ ----------
create table areas (
  code          text primary key,             -- fuk-chuo
  name          text not null,
  stage         smallint not null default 1,  -- 1:福岡市 2:都市圏 3:県内
  osm_relation  bigint,                       -- OSMの行政界
  geom          geography(MultiPolygon, 4326),
  updated_at    timestamptz not null default now()
);
create index areas_geom_idx on areas using gist (geom);

create table landscape_groups (
  code  text primary key,
  name  text not null,
  color text
);

create table spot_categories (
  code            text primary key,
  name            text not null,
  landscape_group text not null references landscape_groups(code),
  shape           text not null default 'point' check (shape in ('point','line')),
  spacing_m       int not null default 0,
  priority        int not null default 100
);

create table tags (
  code  text primary key,
  name  text not null,
  facet text not null
);

-- ---------- 収集の実行記録 ----------
create table data_runs (
  id            bigserial primary key,
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  mode          text not null check (mode in ('dry_run','apply')),
  status        text not null default 'running' check (status in ('running','ok','failed','stopped')),
  source        jsonb,          -- 取得したファイル（URL・MD5・データ時点）
  code_version  text,           -- 実行したコードの版（GitHubのコミット）
  rules_version text,           -- 収集条件の版（categories.py のハッシュ）
  counts        jsonb,          -- 種別×区の件数
  summary       jsonb,          -- 追加・更新・要確認・対象外などの件数
  report        text,           -- 人が読む差分レポート
  error         text
);

-- OSMの要素（点・面は1要素。川・線路などは「種別＋名前」で1つの線）
create table features (
  key            text primary key,           -- n123 / w123 / r123 / river:那珂川
  kind           text not null check (kind in ('element','line')),
  osm_type       char(1),
  osm_id         bigint,
  name           text,
  categories     text[] not null default '{}',
  tags           jsonb,
  geom           geography(Point, 4326),     -- 要素の代表点（線は null）
  extent_m       real not null default 0,
  way_ids        bigint[],                   -- 線を構成するOSMのway
  first_seen_run bigint references data_runs(id),
  last_seen_run  bigint references data_runs(id),
  missing_runs   int not null default 0,     -- 連続で見つからなかった回数
  updated_at     timestamptz not null default now()
);

-- ---------- 地点 ----------
create table spots (
  id               bigserial primary key,
  code             text unique,                      -- FUK-CHU-S0001
  name             text not null,
  alt_names        text[] not null default '{}',
  address          text,                             -- 町丁目まで（国土地理院の逆ジオコーダー）
  address_at       timestamptz,
  area_code        text references areas(code),
  geom             geography(Point, 4326) not null,
  category         text references spot_categories(code),
  extra_categories text[] not null default '{}',
  position_kind    text not null default 'point'
                   check (position_kind in ('point','area','line_sample','standpoint','manual')),
  line_geom        geography(Point, 4326),           -- 線上の位置（standpoint のとき元の位置）
  line_key         text,                             -- river:那珂川
  sample_key       text unique,                      -- river:那珂川#400:12_-3
  origin           text not null default 'osm' check (origin in ('osm','manual')),
  -- 確認の進み具合（人・AI）
  status           text not null default 'candidate' check (status in ('candidate','ai_tagged','scouted','verified')),
  confidence       text not null default 'unverified'
                   check (confidence in ('unverified','ai_estimated','desk_checked','field_checked')),
  -- データの状態（現地確認とは別に管理）
  data_status      text not null default 'current' check (data_status in ('current','needs_review','archived')),
  review_reason    text,                             -- 要確認の理由
  archived_reason  text,
  archived_at      timestamptz,
  archived_run     bigint references data_runs(id),
  missing_runs     int not null default 0,
  last_seen_run    bigint references data_runs(id),
  created_run      bigint references data_runs(id),
  locked           boolean not null default false,   -- 人が位置・名前を直した（自動では変えない）
  era_feel         text check (era_feel in ('showa','heisei','reiwa','mixed','historic')),
  shoot_scope      text default 'unknown' check (shoot_scope in ('exterior_public','exterior_offsite','interior_permit','unknown')),
  owner_type       text default 'unknown' check (owner_type in ('public','private','mixed','unknown')),
  ffc_registered   boolean not null default false,
  ffc_ref          text,
  summary          text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index spots_geom_idx on spots using gist (geom);
create index spots_name_trgm on spots using gin (name gin_trgm_ops);
create index spots_line_key_idx on spots (line_key);
create index spots_live_idx on spots (data_status, category);

create table spot_features (
  spot_id     bigint not null references spots(id) on delete cascade,
  feature_key text not null references features(key),
  role        text not null default 'primary' check (role in ('primary','secondary')),
  linked_run  bigint references data_runs(id),
  primary key (spot_id, feature_key)
);
create index spot_features_feature_idx on spot_features (feature_key);

-- 変化の履歴（自動処理・人の操作のどちらも）
create table spot_history (
  id      bigserial primary key,
  spot_id bigint not null references spots(id) on delete cascade,
  run_id  bigint references data_runs(id),
  at      timestamptz not null default now(),
  actor   text not null default 'pipeline',
  event   text not null,      -- created / moved / renamed / recategorized / missing / archived / restored / linked / needs_review / reviewed / address
  detail  jsonb
);
create index spot_history_spot_idx on spot_history (spot_id, at desc);
create index spot_history_run_idx on spot_history (run_id);

create or replace function touch_updated_at() returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end $$;
create trigger spots_touch before update on spots for each row execute function touch_updated_at();

create table spot_tags (
  spot_id  bigint references spots(id) on delete cascade,
  tag_code text references tags(code),
  origin   text not null default 'ai' check (origin in ('ai','human')),
  score    real default 1.0,
  primary key (spot_id, tag_code)
);

create table spot_practicals (
  spot_id          bigint primary key references spots(id) on delete cascade,
  permit_required  text,
  permit_contact   text,
  restrictions     text,
  past_productions jsonb default '[]',
  notes            text
);

create table spot_photos (
  id          bigserial primary key,
  spot_id     bigint references spots(id) on delete cascade,
  url         text not null,
  shot_at     date,
  shot_by     text,
  heading     smallint,
  time_of_day text,
  license     text default 'own'
);

-- ---------- 流れ（クラスター） ----------
create table clusters (
  id               bigserial primary key,
  code             text unique,
  name             text,
  area_code        text references areas(code),
  transition_score real,
  transition_label text,
  total_minutes    real,
  travel_mode      text check (travel_mode in ('walk','car','mixed')),
  suitable_scenes  text[],
  origin           text not null default 'auto' check (origin in ('auto','human','search')),
  status           text not null default 'candidate' check (status in ('candidate','reviewed','verified','archived')),
  route_key        text unique,
  hit_count        int not null default 0,
  last_hit_at      timestamptz,
  saved_by         text,
  note             text,
  needs_review     boolean not null default false,   -- 含まれる地点が動いた・対象外になった
  review_note      text,
  created_at       timestamptz not null default now()
);

create table cluster_spots (
  cluster_id        bigint references clusters(id) on delete cascade,
  seq               smallint not null,
  spot_id           bigint references spots(id) on delete cascade,
  minutes_from_prev real,
  meters_from_prev  real,
  primary key (cluster_id, seq)
);
create index cluster_spots_spot_idx on cluster_spots (spot_id);

-- ---------- 変化リスク ----------
create table change_risks (
  id          bigserial primary key,
  spot_id     bigint references spots(id) on delete cascade,
  risk_type   text not null check (risk_type in ('redevelopment','demolition','closure','use_change','none')),
  evidence    text,
  source_url  text,
  fetched_at  timestamptz,                -- 根拠ページを取得した日時
  certainty   text not null default 'keyword' check (certainty in ('keyword','confirmed')),
  verified    boolean not null default false,
  detected_at timestamptz not null default now(),
  review_by   date
);

create table sources (
  id           bigserial primary key,
  spot_id      bigint references spots(id) on delete cascade,
  kind         text not null,      -- official / news / field / review
  title        text,
  url          text,
  retrieved_at timestamptz not null default now(),
  note         text
);

create table watch_pages (
  id           bigserial primary key,
  url          text unique not null,
  label        text,
  last_hash    text,
  last_checked timestamptz,
  last_status  text,                -- ok / fetch_failed
  last_error   text
);

create table job_runs (
  id          bigserial primary key,
  run_id      bigint references data_runs(id),
  job         text not null,
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  stats       jsonb,
  error       text
);

create table search_logs (
  id             bigserial primary key,
  kind           text not null default 'route',
  scene          text,
  seq            text[],
  tags           jsonb,
  travel_mode    text,
  area_code      text,
  start_spot     bigint,
  result_count   int,
  top_route_keys text[],
  searched_by    text,
  created_at     timestamptz not null default now()
);
create index search_logs_created_idx on search_logs (created_at desc);

-- 旧版の検索ログは引き継ぐ（地点IDを含まないので、そのまま使える）
do $$ begin
  if exists (select 1 from information_schema.tables where table_schema = 'legacy' and table_name = 'search_logs') then
    insert into search_logs (kind, scene, seq, tags, travel_mode, area_code, result_count, searched_by, created_at)
    select kind, scene, seq, tags, travel_mode, area_code, result_count, searched_by, created_at from legacy.search_logs;
  end if;
end $$;
