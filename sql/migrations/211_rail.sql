-- ============================================================
-- 211: 線路沿い（2026-10-29。番号帯 K）
--  - 景観グループ「線路沿い」（rail）を新設し、種別 線路沿い（railway）と 踏切（level_crossing）を移す。
--    流れ検索で「駅へ向かう」（交通インフラ・駅）と「線路沿いを歩く」を分けるため。grp_match では transport に rail は含めない。
--  - spots.rail_form：線路の形（OSMの bridge・embankment・cutting から。自動収集で毎回付け直す）
--      elevated 高架 / embankment 盛土 / cutting 切り通し / ground 地上
--    地点（線の上の格子の位置）に最も近い線路の区間のタグで決める。線路沿い以外は空。
-- ============================================================
insert into landscape_groups (code, name, color) values ('rail', '線路沿い', '#2B6F8A')
on conflict (code) do update set name = excluded.name, color = excluded.color;
update spot_categories set landscape_group = 'rail' where code in ('railway', 'level_crossing');
alter table spots add column if not exists rail_form text check (rail_form in ('elevated','embankment','cutting','ground'));
