-- ============================================================
-- 207: 写真候補の不採用の理由に「現存しない」を追加（2026-10-23 利用者と合意）
--  closed ＝ 写真を見て、その場所が今はないと分かった。不採用にするとき、確認のうえ地点を「対象外」にできる
--  （sql/api/90_photos.sql の photo_decide の p_archive。地点は消さず、対象外タブから戻せる）
-- ============================================================
do $$
declare c record;
begin
  for c in select conname from pg_constraint
            where conrelid = 'spot_photo_candidates'::regclass and contype = 'c' and pg_get_constraintdef(oid) like '%reject_reason%'
  loop execute format('alter table spot_photo_candidates drop constraint %I', c.conname); end loop;
end $$;
alter table spot_photo_candidates add constraint spot_photo_candidates_reject_reason_check
  check (reject_reason is null or reject_reason in ('sign','person','quality','wrong_place','rights','other','closed'));
