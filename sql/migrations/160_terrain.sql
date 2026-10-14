-- ============================================================
-- 160: 地形・標高（タスクF）
--  国土地理院の標高タイルから、地点ごとに中心＋周囲16点の標高を読み、
--  標高・高台度・最大の傾きを持つ（locadb/terrain.py。自動収集の一工程）。
--  - elevation_m   地点の標高（m）。別ラインで SQL Editor から作った列を引き継ぐ（型はそのまま）。
--                  旧方式で「データなし」に入れていた -1 は空に戻す（全件を取り直す）
--  - relief_m      高台度：地点の標高 − 半径150mの8方向の標高の平均（プラスなら周りより高い）
--  - slope_pct     最大の傾き：半径30mの8方向で最も急な勾配（%）
--  - terrain_src   使った標高データ（dem5a：5m・レーザ / dem5b：5m・写真測量 / dem10b：10m）
--  - terrain_status ok / no_data（海上など、どのデータにも値がない）
--  - terrain_at    取得した日時（これより後に位置が動いたら取り直す）
--  ビュー v_fukuoka_spots（別ラインの LAYA が読む）は一度消し、sql/api/40_terrain.sql で毎回作り直す。
-- ============================================================
drop view if exists v_fukuoka_spots;

-- 本番では別ラインのビューが elevation_m を使っているため、列の型は変えない（本番は numeric のまま。
-- 新しく作るDBでは double precision）。どちらでも小数の標高を入れられる
alter table spots add column if not exists elevation_m double precision;
update spots set elevation_m = null where elevation_m = -1;

alter table spots add column if not exists relief_m       real;
alter table spots add column if not exists slope_pct      real;
alter table spots add column if not exists terrain_src    text;
alter table spots add column if not exists terrain_status text
  check (terrain_status in ('ok','no_data'));
alter table spots add column if not exists terrain_at     timestamptz;
