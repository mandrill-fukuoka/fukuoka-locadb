-- 地形・標高まわり（タスクF）。tools/migrate.py が毎回作り直す
--  v_fukuoka_spots：別ラインの AI（LAYA）が service_role で読むビュー。
--   先頭6列（id, name, address, lat, lng, elevation_m）は別ラインで作った時の形のまま。後ろに列を足すのはよいが、
--   並びや名前は変えない。対象は掲載中の地点すべて（対象外を除く。住所のない島・海辺も含む）
drop view if exists v_fukuoka_spots;
create view v_fukuoka_spots as
select s.id, s.name, s.address,
       ST_Y(s.geom::geometry) as lat, ST_X(s.geom::geometry) as lng,
       s.elevation_m,
       s.code, s.area_code, s.category, s.relief_m, s.slope_pct, s.terrain_status, s.origin, s.data_status
  from spots s
 where s.data_status <> 'archived';
