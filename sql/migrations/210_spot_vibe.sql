-- ============================================================
-- 210: 雰囲気の目安（流れ検索の精度改善 段階2。2026-10-28。番号帯 K＝210〜219）
--  人がタグを付けるのを待たず、DBにある計算済みデータだけで「シーンの条件に合うか」「その景観らしいか」を点数にする。
--  AIは使わない。値はすべて推定（画面では「推定」と書く。撮影の可否は書かない）。
--  - bars_150      半径150m内のバー・居酒屋の地点数（対象外の地点は数えない。自分自身は除く）
--  - eateries_150  半径150m内の飲食店・カフェの地点数（同上）
--  - shops_150     半径150m内の店舗・飲食（景観グループ shop）の地点数（同上）
--  - signals       タグごとの当てはまり度 0〜1（tag_signal。データで測れないタグは入れない）
--  - typicality    その地点の景観グループらしさ 0〜1（group_typicality）
--  - vibe_version  計算方法の版（境界を変えたら上げる）／vibe_at 計算した日時
--  付け直しは vibe_apply()（自動収集の空間特徴・ルールのタグの後。Actions「vibe-apply」からも流せる）。
--  境界は sql/api/85_vibe.sql の関数の中の1か所だけに書く。
-- ============================================================
alter table spot_spatial add column if not exists bars_150     integer;
alter table spot_spatial add column if not exists eateries_150 integer;
alter table spot_spatial add column if not exists shops_150    integer;
alter table spot_spatial add column if not exists signals      jsonb;
alter table spot_spatial add column if not exists typicality   real;
alter table spot_spatial add column if not exists vibe_version text;
alter table spot_spatial add column if not exists vibe_at      timestamptz;
