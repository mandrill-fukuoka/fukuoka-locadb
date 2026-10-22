-- ============================================================
-- 201: Mapillary の街路写真を写真候補に加える（2026-10-22 利用者が実装を決定）
--  - 利用条件：Mapillary の画像は CC BY-SA 4.0。作者（ユーザー名）・ライセンス・Mapillary の画像ページへのリンクを必ず表示する
--  - 画像は保存しない（API が返す縮小画像のURLを参照する）。候補は人が採用してはじめて公開（Commons と同じ photo_decide）
--  - アクセストークンは GitHub Secrets（MAPILLARY_TOKEN）だけに置く。DB・リポジトリ・ログには出さない
-- ============================================================
alter table spot_photo_candidates drop constraint if exists spot_photo_candidates_source_check;
alter table spot_photo_candidates add constraint spot_photo_candidates_source_check check (source in ('commons','mapillary'));

-- Mapillary を探した地点の記録（Commons の spot_photo_search とは別に持つ）
create table if not exists spot_photo_search_mly (
  spot_id     bigint primary key references spots(id) on delete cascade,
  searched_at timestamptz not null default now(),
  found       int not null default 0
);
