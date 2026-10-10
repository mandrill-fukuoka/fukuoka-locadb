-- ============================================================
-- 009: 橋の整理と、旧方式の収集データの後片付け
-- これまで「名前付きの橋」を全部拾っていたため、高速道路・幹線道路の高架
-- （道路名が付いているだけの区間）が大量に入り、重複の疑いの大半を占めていた。
-- 未確認の橋の地点をいったん外し、次の収集で新しい条件
-- （〇〇橋・橋名タグ・名前のある歩道橋や人道橋）に合うものだけを入れ直す。
-- Supabase の SQL Editor に全文を貼り付けて Run（何度実行しても安全）
-- ※ 人が確認・ロケハン済みにした地点（scouted / verified）は外しません
-- ============================================================

-- 1) 未確認の橋の地点を「対象外」にする
update spots set status = 'archived'
 where category = 'bridge_overpass' and status in ('candidate', 'ai_tagged');

-- 2) 外した橋の収集データを削除（次の収集で、新しい条件に合うものだけ取り直す）
delete from raw_candidates rc
 using spots s
 where rc.spot_id = s.id and s.status = 'archived' and s.category = 'bridge_overpass';

-- 3) 旧方式（線を1点で表していた頃）の収集データの残りを削除
--    別の種別の地点（駅など）に統合されていたため 008 で消えずに残ったもの。
--    同じ地点には新方式の収集データも付いているので、地点自体は残る。
delete from raw_candidates
 where source = 'osm'
   and category_hint in ('river', 'coast', 'railway', 'shotengai', 'public_space', 'bridge_overpass')
   and (source_ref like 'osm:way/%' or source_ref like 'osm:relation/%')
   and source_ref not like '%@%'
   and not exists (select 1 from spots s where s.id = raw_candidates.spot_id
                    and s.status in ('scouted', 'verified'));   -- 人が確認した地点の出典は残す

-- 4) 自動生成のクラスターで、外した地点を含むものを削除（次の収集で作り直す）
delete from clusters c
 where c.origin = 'auto' and c.status = 'candidate'
   and exists (select 1 from cluster_spots cs join spots s on s.id = cs.spot_id
                where cs.cluster_id = c.id and s.status = 'archived');

-- 結果の確認
select (select count(*) from spots where category = 'bridge_overpass' and status <> 'archived') as 残った橋の地点,
       (select count(*) from spots where status <> 'archived')                                  as 地点の合計;
