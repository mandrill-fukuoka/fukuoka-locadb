-- ============================================================
-- 202: ロジ情報・同録の騒音の目安（修正提案3 層1。2026-10-22 利用者と合意）
--  OSMから計算した「最寄りの施設・騒音源までの距離（m）」を spot_spatial に足す（locadb/spatial.py。SPATIAL_VERSION=spatial-3）
--  事実（OSMに載っているものだけ。載っていなければ実際より遠く出る）。AI・外部データは使わない
--  ロジ：toilet_m 公衆トイレ / convenience_m コンビニ / parking_m 駐車場（私有・路肩は除く）
--  騒音源：rail_m 地上の線路（rail・light_rail・tram）/ major_road_m 幹線道路（高速・国道・主要地方道＝motorway〜primary）/ runway_m 滑走路
--  段階（近い・やや近い）は画面側（web/app/lib.php）に書く。暫定の値（仕様書10章）
-- ============================================================
alter table spot_spatial add column if not exists toilet_m       real;
alter table spot_spatial add column if not exists convenience_m  real;
alter table spot_spatial add column if not exists parking_m      real;
alter table spot_spatial add column if not exists rail_m         real;
alter table spot_spatial add column if not exists major_road_m   real;
alter table spot_spatial add column if not exists runway_m       real;
