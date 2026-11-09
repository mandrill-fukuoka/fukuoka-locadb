-- ============================================================
-- 241: 種別 sento（温泉・公衆浴場）を景観グループ bath に付け替える（2026-11-09。番号帯 N）
--  locadb/categories.py の masters（自動収集）と同じ値。次の月曜の pipeline を待たずに反映するための一度きりの更新。
--  地点は種別コード sento のまま（spots は触らない）。景観らしさ（spot_vibe）は vibe-apply で付け直す
-- ============================================================
update spot_categories set landscape_group = 'bath', name = '温泉・公衆浴場' where code = 'sento';
