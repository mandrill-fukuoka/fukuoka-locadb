-- ============================================================
-- 220: 追加候補にBODIK（福岡市オープンデータ）を加える（2026-11-05 利用者と合意。番号帯L＝220〜229）
--  - 福岡市がBODIK ODCS（data.bodik.jp、CC BY 4.0）で公開している施設・公園などの一覧から、「DBにまだない場所」を追加候補にする
--  - 追加候補（203）の仕組みをそのまま使う。候補は公開しない。確認画面の「追加候補」で人が名前・種別を確かめて採用したときだけ地点になる
--  - 列を足すだけ：dataset（どの一覧から来たか）、detail（一覧の種別・面積など。氏名や連絡先は入れない）、sort_rank（画面での並び順。大きいほど先）
--  - 住所だけの一覧（旅館業・公衆浴場・特定建築物・飲食店営業許可）は国土地理院の住所検索で座標を出す。目安の位置なので detail.geocode に残す
-- ============================================================
alter table spot_additions drop constraint if exists spot_additions_source_check;
alter table spot_additions add constraint spot_additions_source_check check (source in ('wikipedia','commons','bodik'));
alter table spot_additions add column if not exists dataset   text;
alter table spot_additions add column if not exists detail    jsonb not null default '{}'::jsonb;
alter table spot_additions add column if not exists sort_rank double precision not null default 0;
create index if not exists spot_additions_dataset_idx on spot_additions (status, dataset, sort_rank desc, id) where source = 'bodik';
