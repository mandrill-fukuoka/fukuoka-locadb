-- 雰囲気の目安（流れ検索の精度改善 段階2、DB変更210。tools/migrate.py が毎回作り直す）
--  DBにある計算済みデータ（空間特徴・地形・種別・周りの店の数）だけで、タグの当てはまり度と景観らしさを 0〜1 で出す。
--  AIは使わない。AIのタグは使わない。人が付けたタグがあればそちらを優先（tag_signal）。
--  境界（暫定。2026-10-28 指示書の案。本番の分布を見て利用者と決める）は次の2か所だけに書く：
--    タグの当てはまり度 … vibe_signals
--    景観らしさ         … group_typicality
--  変えたら vibe_version を上げ、vibe_apply を流し直す（tests/test_routes.py も合わせる）。
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in (
              'vibe_version','clamp01','scene_signal','vibe_signals','tag_signal','group_typicality','vibe_apply','vibe_stats','vibe_line')
  loop execute format('drop function %s', f.sig); end loop;
end $$;

create function vibe_version() returns text language sql immutable as $$ select 'vibe-v1'::text $$;

-- 0〜1 に収め、小数3桁にする
create function clamp01(v double precision) returns numeric language sql immutable as $$
  select case when v is null then null else round(greatest(0, least(1, v))::numeric, 3) end;
$$;

-- タグごとの当てはまり度（データで測れるタグだけ）。周りの店の数は spot_spatial に保存した値を使う（vibe_apply が先に数える）。
--  空間特徴が信頼できない地点（confidence=low）・未計算の項目は、そのタグを入れない（＝点数に使わない）
create function vibe_signals(p_id bigint) returns jsonb language sql stable as $$
with x as (
  select s.category as cat, sc.landscape_group as grp, s.relief_m,
         ss.bars_150 as bars, ss.eateries_150 as eat, ss.shops_150 as shops,
         case when ss.confidence = 'ok' then ss.open_ratio end as open_r,
         case when ss.confidence = 'ok' then ss.building_cover_100 end as cover,
         case when ss.confidence = 'ok' then sp_road(ss.road_class, ss.road_width_m) end as road,
         ss.water_dist_m as wd, ss.water_kind as wk, ss.spot_id is not null as has_ss
    from spots s join spot_categories sc on sc.code = s.category
    left join spot_spatial ss on ss.spot_id = s.id
   where s.id = p_id
), v as (
  select x.*,
         -- 店が少ないほど高い（0〜2軒で1.0）
         case when shops is null then null when shops <= 2 then 1.0 when shops <= 5 then 0.6 when shops <= 15 then 0.3 else 0 end as few_shops,
         case when road = 'narrow' then 1.0 when road = 'normal' then 0.3 when road = 'wide' then 0 end as narrow,
         -- 水辺の近さ（50m未満で1.0、150m未満で0.5）
         case when not has_ss then null when wd is null or wd >= 150 then 0 when wd < 50 then 1.0 else 0.5 end as near_w
    from x
)
select jsonb_strip_nulls(jsonb_build_object(
  'night_ok',  clamp01(case when bars is null then null else 0.7 * least(bars / 10.0, 1) + 0.3 * least(eat / 20.0, 1)
                         + case when cat in ('eatery_street','shotengai') then 0.2 else 0 end end),
  'neon',      clamp01(case when bars is null then null else least(bars / 10.0, 1)
                         + case when cat = 'eatery_street' then 0.2 else 0 end end),
  'lively',    clamp01(case when shops is null then null else 0.8 * least(shops / 30.0, 1) + 0.2 * coalesce(least(cover / 0.3, 1), least(shops / 30.0, 1)) end),
  'high_traffic', clamp01(case when shops is null then null else 0.8 * least(shops / 30.0, 1) + 0.2 * coalesce(least(cover / 0.3, 1), least(shops / 30.0, 1)) end),
  'lonely',    clamp01(case when few_shops is null then null else 0.6 * few_shops
                         + case when grp in ('residential','green','river','sea','lake','beach') then 0.2 else 0 end
                         + 0.2 * coalesce(narrow, 0) end),
  'low_traffic', clamp01(case when few_shops is null then null else 0.6 * few_shops
                         + case when grp in ('residential','green','river','sea','lake','beach') then 0.2 else 0 end
                         + 0.2 * coalesce(narrow, 0) end),
  'calm',      clamp01(case when few_shops is null then null else 0.6 * few_shops
                         + case when grp in ('residential','green','river','sea','lake','beach') then 0.4 else 0 end end),
  'narrow_street', clamp01(case when cat = 'alley' then 1.0 else narrow end),
  'wide_open', clamp01(case when open_r is null then null else (open_r / 0.7)::numeric end),
  'river_view', clamp01(case when cat = 'river' then 1.0 when near_w is null then null
                             when wk = 'river' then near_w else 0 end),
  'sea_view',  clamp01(case when cat in ('coast','beach') then 1.0 when near_w is null then null
                             when wk in ('coast','beach') then near_w else 0 end),
  'skyline',   clamp01(case when cat = 'new_building' then 1.0 when cover is null then null else 0.5 * least(cover / 0.35, 1) end),
  'futuristic', clamp01(case when cat = 'new_building' then 1.0 when cover is null then null else 0.5 * least(cover / 0.35, 1) end),
  'industrial_structure', clamp01(case when cat in ('factory','port','warehouse') then 1.0 when cat = 'fishing_port' then 0.6 else 0 end),
  'inorganic', clamp01(case when cat in ('factory','port','warehouse','new_building') then 1.0 when cat = 'fishing_port' then 0.6 else 0 end),
  'gritty',    clamp01(case when cat in ('factory','port','warehouse') then 1.0 when cat in ('fishing_port','vacant') then 0.6 else 0 end),
  'slope_stairs', clamp01(case when cat = 'slope_street' then 1.0 when relief_m is null then 0
                               when relief_m >= 15 then 0.6 when relief_m >= 5 then 0.3 else 0 end),
  'greenery',  clamp01(case when cat in ('park','hill') then 1.0 else 0 end)
))
from v;
$$;

-- タグの当てはまり度：人が付けたタグがあればそれ（付いていれば1.0）、なければ保存した推定値。AIのタグは使わない。
--  データで測れないタグ（nostalgic・romantic・tense・showa など）や未計算の地点は null（点数に使わない）
create function tag_signal(p_id bigint, p_tag text) returns real language sql stable as $$
  select coalesce(
    (select least(coalesce(t.score, 1), 1)::real from spot_tags t where t.spot_id = p_id and t.tag_code = p_tag and t.origin = 'human'),
    (select (ss.signals ->> p_tag)::real from spot_spatial ss where ss.spot_id = p_id));
$$;

-- シーンの条件の当てはまり度（1地点）：指定タグの当てはまり度の重み付き平均。測れるタグがなければ null。
--  p_human が真の地点だけ人のタグを引く（tag_signal と同じ結果。検索を速くするため）
create function scene_signal(p_id bigint, p_signals jsonb, p_tags jsonb, p_human boolean) returns real language sql stable as $$
  select (sum(w * v) / nullif(sum(w), 0))::real
    from (select e.value::text::real as w,
                 case when p_human then tag_signal(p_id, e.key) else (p_signals ->> e.key)::real end as v
            from jsonb_each(coalesce(p_tags, '{}'::jsonb)) e) z
   where v is not null and w > 0;
$$;

-- 景観らしさ：その景観グループらしい地点ほど高い。データがなければ null、上の一覧にないグループは 0.5（中立）
create function group_typicality(p_id bigint, p_grp text) returns real language sql stable as $$
with x as (
  select s.category as cat, s.position_kind as pk, s.slope_grade_pct as grade, s.relief_m,
         ss.bars_150 as bars, ss.shops_150 as shops, ss.water_dist_m as wd, ss.spot_id is not null as has_ss,
         case when ss.confidence = 'ok' then ss.open_ratio end as open_r,
         case when ss.confidence = 'ok' then ss.building_cover_100 end as cover,
         case when ss.confidence = 'ok' then sp_road(ss.road_class, ss.road_width_m) end as road
    from spots s left join spot_spatial ss on ss.spot_id = s.id
   where s.id = p_id
)
select (case
  -- 繁華街・商業：周りの店と飲み屋の多さ（郊外の大きな商業施設で周りに店がない地点は低い）
  when p_grp = 'commercial' then clamp01(case when shops is null then null else
         0.6 * least(shops / 30.0, 1) + 0.4 * least(bars / 10.0, 1) + case when cat = 'eatery_street' then 0.2 else 0 end end)
  -- 古い街並み・路地：狭い道・建物が密・商店街
  when p_grp = 'old_town' then clamp01(case when road is null and cover is null then
           case when cat in ('alley','shotengai') then 0.6 end else
         0.4 * coalesce(case road when 'narrow' then 1.0 when 'normal' then 0.3 else 0 end, 0)
       + 0.3 * coalesce(least(cover / 0.3, 1), 0)
       + 0.3 * case when cat in ('alley','shotengai') then 1.0 else least(coalesce(shops, 0) / 15.0, 1) end end)
  -- 水辺：水辺までの距離（立てる場所 standpoint は加点）
  when p_grp in ('river','sea','lake','beach','water') then clamp01(case when not has_ss then null else
         (case when wd is null then 0.1 when wd < 30 then 1.0 when wd < 50 then 0.85 when wd < 80 then 0.6 when wd < 150 then 0.35 else 0.1 end)
       + case when pk = 'standpoint' then 0.1 else 0 end end)
  -- 港湾・工業：工業構造物と開放感
  when p_grp = 'port_industrial' then clamp01(
         0.6 * case when cat in ('factory','port','warehouse') then 1.0 else 0.3 end + 0.4 * coalesce(open_r, 0.5))
  -- 坂道：勾配・高低差
  when p_grp = 'slope' then clamp01(case when grade is not null then
           case when grade >= 10 then 1.0 when grade >= 5 then 0.7 else 0.4 end
         when relief_m is not null then case when relief_m >= 10 then 0.8 when relief_m >= 3 then 0.5 else 0.3 end end)
  else 0.5 end)::real
from x;
$$;

-- 付け直し：周りの店の数を数え、当てはまり度と（自分の景観グループの）景観らしさを保存する。
--  自動収集の空間特徴・ルールのタグの後に呼ぶ（locadb/vibe.py）。空間特徴の行がある地点だけが対象
create function vibe_apply() returns jsonb language plpgsql as $$
declare v_n int;
begin
  update spot_spatial ss set
         bars_150     = c.bars, eateries_150 = c.eat, shops_150 = c.shops
    from (select s.id,
                 count(o.id) filter (where o.category = 'bar') as bars,
                 count(o.id) filter (where o.category in ('restaurant','cafe')) as eat,
                 count(o.id) filter (where oc.landscape_group = 'shop') as shops
            from spots s
            join spot_spatial x on x.spot_id = s.id
            left join spots o on o.id <> s.id and o.data_status <> 'archived' and ST_DWithin(o.geom, s.geom, 150)
            left join spot_categories oc on oc.code = o.category
           group by s.id) c
   where ss.spot_id = c.id;
  update spot_spatial ss set signals = vibe_signals(ss.spot_id),
         typicality = group_typicality(ss.spot_id, sc.landscape_group),
         vibe_version = vibe_version(), vibe_at = now()
    from spots s join spot_categories sc on sc.code = s.category
   where s.id = ss.spot_id;
  get diagnostics v_n = row_count;
  return jsonb_build_object('updated', v_n, 'version', vibe_version()) || vibe_stats();
end $$;

-- 分布（読み取りのみ）：店の数の 10・50・90・99%点と、タグごとの当てはまり度の平均・0.5以上の地点数
--  未保存（vibe_apply 前）でも店の数はその場で数える（境界を決める材料。dry_run 用）
create function vibe_stats() returns jsonb language sql stable as $$
with c as (
  select s.id, sc.landscape_group as grp,
         coalesce(ss.bars_150, (select count(*) from spots o where o.id <> s.id and o.data_status <> 'archived'
                                   and o.category = 'bar' and ST_DWithin(o.geom, s.geom, 150))) as bars,
         coalesce(ss.eateries_150, (select count(*) from spots o where o.id <> s.id and o.data_status <> 'archived'
                                   and o.category in ('restaurant','cafe') and ST_DWithin(o.geom, s.geom, 150))) as eat,
         coalesce(ss.shops_150, (select count(*) from spots o join spot_categories oc on oc.code = o.category
                                  where o.id <> s.id and o.data_status <> 'archived' and oc.landscape_group = 'shop'
                                    and ST_DWithin(o.geom, s.geom, 150))) as shops,
         ss.signals, ss.typicality
    from spots s join spot_categories sc on sc.code = s.category
    join spot_spatial ss on ss.spot_id = s.id
   where s.data_status <> 'archived' and sc.landscape_group <> 'shop'
)
select jsonb_build_object(
  'spots', (select count(*) from c),
  'percentiles_10_50_90_99', jsonb_build_object(
     'bars_150',     (select to_jsonb(percentile_disc(array[.1,.5,.9,.99]) within group (order by bars)) from c),
     'eateries_150', (select to_jsonb(percentile_disc(array[.1,.5,.9,.99]) within group (order by eat)) from c),
     'shops_150',    (select to_jsonb(percentile_disc(array[.1,.5,.9,.99]) within group (order by shops)) from c)),
  'signals', (select coalesce(jsonb_object_agg(k, jsonb_build_object('n', n, 'avg', a, 'ge_0_5', g)), '{}'::jsonb) from (
       select e.key as k, count(*) as n, round(avg(e.value::text::numeric), 2) as a,
              count(*) filter (where e.value::text::numeric >= 0.5) as g
         from c cross join jsonb_each(coalesce(c.signals, '{}'::jsonb)) e group by e.key) t),
  'typicality_by_group', (select coalesce(jsonb_object_agg(grp, jsonb_build_object('n', n, 'avg', a, 'ge_0_5', g)), '{}'::jsonb) from (
       select grp, count(typicality) as n, round(avg(typicality)::numeric, 2) as a,
              count(*) filter (where typicality >= 0.5) as g from c group by grp) t));
$$;

-- 流れの各カットに出す1行（すべて推定。撮影の可否は書かない）。例：「周り150mにバー12軒・狭い道（推定）」
create function vibe_line(p_id bigint) returns text language sql stable as $$
  select nullif(concat_ws('・',
           case when ss.bars_150 > 0 then '周り150mにバー・居酒屋' || ss.bars_150 || '軒'
                when ss.shops_150 > 0 then '周り150mに店' || ss.shops_150 || '軒'
                when ss.shops_150 = 0 then '周り150mに店なし' end,
           case when ss.confidence = 'ok' and sp_road(ss.road_class, ss.road_width_m) = 'narrow' then '狭い道' end,
           case when ss.confidence = 'ok' and sp_open(ss.open_ratio) = 'open' then '開けている' end,
           case sp_water(ss.water_dist_m) when 'near' then '水辺まで50m未満' when 'close' then '水辺まで150m未満' end), '')
         || '（推定）'
    from spot_spatial ss where ss.spot_id = p_id;
$$;
