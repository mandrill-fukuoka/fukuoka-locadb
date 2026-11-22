-- 変化の材料（順6後半。2026-11-22）：地点の状態が変わった兆しを、公開データの「差」から拾う（AIなし・費用ゼロ）
--  * OSM のタグ：廃業・閉鎖（disused:/abandoned:/was:/end_date）、解体（demolished:/razed:/removed:）、工事（construction）の印が
--    新しく付いたとき。前回の印を feature_marks に覚えておき、増えた分だけを変化リスクにする
--  * Overture の店の点：リリースが変わったとき、前回あって今回ない点を place_points_gone に残し、地点の周り50mで
--    「3件以上、かつ6割以上」が消えた地点を変化リスクにする
-- どちらも change_risks に「要確認」（certainty=keyword）で入る。人が一次資料で確認する前提で、自動では確認済みにしない
create table feature_marks (
  feature_key text primary key references features(key) on delete cascade,
  marks       text[] not null,                       -- redevelopment / demolition / closure
  updated_at  timestamptz not null default now()
);

create table place_points_gone (
  gers_id      text primary key,
  cls          text not null,
  cat          text,
  geom         geography(Point, 4326) not null,
  release_from text,
  release_to   text
);
create index place_points_gone_geom_idx on place_points_gone using gist (geom);

-- どの材料で見つけた変化リスクか（page=監視ページ／osm_tags=OSMのタグの変化／overture=店の点の消失）
alter table change_risks add column source_kind text not null default 'page'
  check (source_kind in ('page','osm_tags','overture'));
