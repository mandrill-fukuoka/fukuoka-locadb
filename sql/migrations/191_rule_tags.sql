-- ============================================================
-- 191: ルールで付けるタグ（タスクI。2026-10-18 利用者と合意）
--  spot_tags.origin に 'rule' を足す。AIでも人でもなく、位置・種別・空間特徴から機械的な規則で付けたタグ。
--  規則は sql/api/80_rule_tags.sql（rule_tags_apply）。パイプラインの一工程として毎回付け直す。
--  人・AIが同じタグを付けている地点には付けない（主キーが同じ）。画面には「推定」として出す。
-- ============================================================
do $$
declare c record;
begin
  for c in select conname from pg_constraint where conrelid = 'spot_tags'::regclass and contype = 'c'
              and pg_get_constraintdef(oid) like '%origin%'
  loop execute format('alter table spot_tags drop constraint %I', c.conname); end loop;
end $$;
alter table spot_tags add constraint spot_tags_origin_check check (origin in ('ai','human','rule'));
