-- ============================================================
-- 162: 坂道（タスクF）
--  OSMの道路・階段に沿って標高を読み、急な区間を種別「坂道・階段坂」（slope_street）の地点にする
--  （locadb/slopes.py。収集の中で動き、川・商店街と同じ「線の地点」として反映）。
--  新しい景観「坂道・高低差」（slope）を追加（利用者の了承済み 2026-10-14）。色はほかの景観と見分けられるオリーブ。
--  坂道の地点の測定値：
--  - slope_len_m      坂の長さ（m）
--  - slope_grade_pct  平均勾配（%）
--  - slope_rise_m     上り下りの高低差（m）
--  - slope_bearing    上りの方角（北0・東90・南180・西270）
--  - slope_kind       road（道）/ steps（階段）
-- ============================================================
insert into landscape_groups (code, name, color) values ('slope', '坂道・高低差', '#8A8A2E')
on conflict (code) do update set name = excluded.name, color = excluded.color;

alter table spots add column if not exists slope_len_m     real;
alter table spots add column if not exists slope_grade_pct real;
alter table spots add column if not exists slope_rise_m    real;
alter table spots add column if not exists slope_bearing   smallint check (slope_bearing between 0 and 359);
alter table spots add column if not exists slope_kind      text check (slope_kind in ('road','steps'));
