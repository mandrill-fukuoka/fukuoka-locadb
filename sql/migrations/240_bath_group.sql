-- ============================================================
-- 240: 温泉・公衆浴場（2026-11-09 利用者の依頼。番号帯 N＝240〜249 を新設）
--  温泉・公衆浴場（OSM amenity=public_bath。種別コード sento）が景観グループ「古い街並み・路地」に混ざっていたので、
--  専用の景観グループ「温泉・公衆浴場」（bath）に分ける。種別 sento の所属は locadb/categories.py の自動収集（masters）で付け替わる。
--  このファイルは先にグループを作るだけ（spot_categories が参照するので、pipeline の apply より先に適用する）。色は他の景観と見分けられる珊瑚色
-- ============================================================
insert into landscape_groups (code, name, color) values ('bath', '温泉・公衆浴場', '#D9667A')
on conflict (code) do update set name = excluded.name, color = excluded.color;
