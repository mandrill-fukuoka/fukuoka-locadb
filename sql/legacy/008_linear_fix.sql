-- ============================================================
-- 008: 川・海岸線・線路・商店街など「細長いもの」の位置ずれを修正
-- これまで線を囲む四角形の中心を位置にしていたため、曲がった川などで
-- 地点が川から外れていた。該当する地点をいったん外し、次の収集で
-- 線に沿った正しい位置（川なら約400mおき）に入れ直す。
-- Supabase の SQL Editor に全文を貼り付けて Run（何度実行しても安全）
-- ※ 人が確認・ロケハン済みにした地点（scouted / verified）は外しません
-- ============================================================

-- 1) 位置ずれの可能性がある地点を「対象外」にする（線データ由来・旧方式・未確認のもの）
with old as (
  select s.id
    from spots s
   where s.category in ('river', 'coast', 'railway', 'shotengai', 'public_space')
     and s.status in ('candidate', 'ai_tagged')
     and exists (select 1 from raw_candidates rc
                  where rc.spot_id = s.id
                    and rc.source = 'osm'
                    and (rc.source_ref like 'osm:way/%' or rc.source_ref like 'osm:relation/%')
                    and rc.source_ref not like '%@%')
)
update spots set status = 'archived' where id in (select id from old);

-- 2) 旧方式の収集データを削除（次の収集で、線に沿った位置で取り直す）
delete from raw_candidates rc
 using spots s
 where rc.spot_id = s.id and s.status = 'archived'
   and s.category in ('river', 'coast', 'railway', 'shotengai', 'public_space')
   and rc.source_ref not like '%@%';

-- 3) 自動生成のクラスターで、外した地点を含むものを削除（次の収集で作り直す）
delete from clusters c
 where c.origin = 'auto' and c.status = 'candidate'
   and exists (select 1 from cluster_spots cs join spots s on s.id = cs.spot_id
                where cs.cluster_id = c.id and s.status = 'archived');

-- 4) 場所を探す：同じ名前の川などは1件にまとめ、「ほか○か所」を返す
create or replace function search_spots(
  p_q text default null, p_area text default null, p_category text default null,
  p_group text default null, p_tags text[] default null, p_offset int default 0, p_limit int default 40
) returns jsonb language sql stable as $$
with hits as (
  select s.id, s.code, s.name, s.area_code, s.address, s.category as cat, sc.name as category, lg.name as grp,
         s.era_feel, s.shoot_scope, s.status, s.confidence, s.summary, s.ffc_registered, s.geom,
         (s.name like '（無名）%') as unnamed
    from spots s
    join spot_categories sc on sc.code = s.category
    join landscape_groups lg on lg.code = sc.landscape_group
   where s.status <> 'archived'
     and (p_q is null or s.name ilike '%' || p_q || '%' or s.address ilike '%' || p_q || '%'
          or array_to_string(s.alt_names, ' ') ilike '%' || p_q || '%' or similarity(s.name, p_q) > 0.3)
     and (p_area is null or s.area_code = p_area)
     and (p_category is null or s.category = p_category)
     and (p_group is null or grp_match(p_group, sc.landscape_group))
     and (p_tags is null or cardinality(p_tags) = 0 or exists (
            select 1 from spot_tags st where st.spot_id = s.id and st.tag_code = any (p_tags)))
),
grouped as (
  select h.*,
         count(*) over (partition by case when h.unnamed then h.id::text else h.name || '|' || h.cat end) as same_name_count,
         row_number() over (partition by case when h.unnamed then h.id::text else h.name || '|' || h.cat end
                            order by (h.confidence in ('desk_checked','field_checked')) desc, h.id) as rn
    from hits h
)
select coalesce(jsonb_agg(x), '[]'::jsonb) from (
  select g.id, g.code, g.name, g.area_code, g.address, g.category, g.grp,
         g.era_feel, g.shoot_scope, g.status, g.confidence, g.summary, g.ffc_registered,
         ST_Y(g.geom::geometry) as lat, ST_X(g.geom::geometry) as lon,
         g.same_name_count - 1 as others,
         (select jsonb_agg(t.name order by st.score desc) from spot_tags st join tags t on t.code = st.tag_code
           where st.spot_id = g.id and st.score >= 0.5) as tags,
         exists (select 1 from change_risks rk where rk.spot_id = g.id and rk.risk_type <> 'none') as risk
    from grouped g
   where g.rn = 1
   order by g.unnamed,
            case when p_q is null then 0 else similarity(g.name, p_q) end desc,
            (g.confidence in ('desk_checked','field_checked')) desc,
            g.code
   offset p_offset limit p_limit
) x;
$$;

do $$
begin
  revoke execute on function search_spots(text,text,text,text,text[],integer,integer) from public;
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke execute on function search_spots(text,text,text,text,text[],integer,integer) from anon; end if;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant execute on function search_spots(text,text,text,text,text[],integer,integer) to service_role; end if;
end $$;

-- 結果確認：外した地点の数（種別ごと）
select sc.name as 種別, count(*) as 外した地点数
  from spots s join spot_categories sc on sc.code = s.category
 where s.status = 'archived' and s.category in ('river', 'coast', 'railway', 'shotengai', 'public_space')
 group by sc.name order by 2 desc;
