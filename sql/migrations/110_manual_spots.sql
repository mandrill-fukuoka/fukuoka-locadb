-- ============================================================
-- 110: 地点の手入力（タスクA）
--  - spots.osm_seen：保護された地点（人が確認・修正した地点）について、収集が前回見たOSMの値。
--    収集は「地点の今の値」ではなくこれと比べ、OSMが前回から変わったときだけ要確認にする
--    （人が直した位置や、要確認を解消した地点が毎週ぶり返さないように）。
--    形：{"name":…, "lon":…, "lat":…, "cats":[…]} または {"missing": "理由"}
--  - spot_practicals：誰がいつ更新したか
--  - 手入力の地点のコード（FUK-<区>-M0001）を引くための索引
-- ============================================================
alter table spots add column if not exists osm_seen jsonb;

alter table spot_practicals add column if not exists updated_at timestamptz;
alter table spot_practicals add column if not exists updated_by text;

create index if not exists spots_origin_idx on spots (origin) where origin = 'manual';
