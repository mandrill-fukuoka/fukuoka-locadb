-- ============================================================
-- 208: 境内・敷地内の地点の親子（2026-10-24 利用者と合意）
--  寺社の境内、大きな公園（2ha以上）・大学の敷地の中にある地点に親を付ける（locadb/containment.py。毎回の収集で付け直す）。
--  地点は消さない。一覧・検索では group_root_id ごとに1件にまとめて「ほか○か所」、地点ページで敷地内の地点を並べる。
--  parent_source：osm（自動）／ manual（人が付けた。自動では変えない）／ none（人が「まとめない」とした。自動で付けない）
-- ============================================================
alter table spots
  add column parent_id     bigint references spots(id) on delete set null,
  add column group_root_id bigint references spots(id) on delete set null,
  add column parent_source text check (parent_source in ('osm', 'manual', 'none'));
create index spots_parent_idx on spots (parent_id) where parent_id is not null;
create index spots_group_root_idx on spots (group_root_id) where group_root_id is not null;
