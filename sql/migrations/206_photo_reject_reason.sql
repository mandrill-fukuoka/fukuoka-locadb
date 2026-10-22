-- ============================================================
-- 206: 写真候補の不採用の理由（2026-10-22 利用者と合意）
--  不採用にしたとき、理由を選べるようにする（次回の「おすすめ度」のルールを確かな傾向から作るため）。
--  sign 看板・標識・地図 / person 人が写っている / quality 画質・構図 / wrong_place 別の場所 / rights 商標・権利 / other その他
--  理由は任意（空でもよい）。おすすめ度のルールは sql/api/90_photos.sql の photo_suggest（暫定）
-- ============================================================
alter table spot_photo_candidates add column if not exists reject_reason text
  check (reject_reason is null or reject_reason in ('sign','person','quality','wrong_place','rights','other'));
