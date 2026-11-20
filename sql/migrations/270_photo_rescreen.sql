-- ============================================================
-- 270: 写真候補のふるい直し（順4。作業方針 5章。番号帯 270〜279）
--  地点名のローマ字・英語名、Commons のカテゴリ名での照合の結果（match_kind）と、候補のカテゴリ名を持つ。
--  match_kind：ja 日本語の名前／romaji ローマ字・英語名／category Commons のカテゴリ名。null は一致なし
--  同じ写真が複数の地点に出るときの2か所目以降は、decided_by = 'auto:dedup'・理由 wrong_place で不採用にする（人が決めたものは変えない）
-- ============================================================
alter table spot_photo_candidates add column if not exists match_kind   text check (match_kind is null or match_kind in ('ja','romaji','category'));
alter table spot_photo_candidates add column if not exists categories   text[];
alter table spot_photo_candidates add column if not exists rescreened_at timestamptz;
