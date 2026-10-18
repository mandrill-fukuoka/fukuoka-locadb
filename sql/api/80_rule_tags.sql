-- ルールで付けるタグ（タスクI、DB変更191。tools/migrate.py が毎回作り直す）
--  付けるのは、位置・種別・空間特徴から「事実として言える」ものだけ。雰囲気（寂しい・穏やか等）の主観の語は付けない。
--  規則（変えるときはこの関数と tests/test_spatial.py）：
--    wide_open            空間特徴が信頼でき、開放性が「開けている」（sp_open）
--    narrow_street        空間特徴が信頼でき、最寄りの道が「狭い道」（sp_road）
--    greenery             種別が 公園 / 丘陵・展望
--    industrial_structure 種別が 工場 / 港湾 / 漁港 / 倉庫
--    slope_stairs         種別が 坂道
--  毎回、origin='rule' のタグだけを付け直す（人・AIのタグは触らない。同じタグが既にあればそちらを優先）
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in ('rule_tags_apply')
  loop execute format('drop function %s', f.sig); end loop;
end $$;

create function rule_tags_apply() returns jsonb language plpgsql as $$
declare v_added int; v_removed int; v_by jsonb;
begin
  drop table if exists _rt;
  create temp table _rt on commit drop as
  select s.id as spot_id, r.tag_code
    from spots s
    left join spot_spatial ss on ss.spot_id = s.id and ss.confidence = 'ok'
    cross join lateral (values
      ('wide_open',            sp_open(ss.open_ratio) = 'open'),
      ('narrow_street',        sp_road(ss.road_class, ss.road_width_m) = 'narrow'),
      ('greenery',             s.category in ('park','hill')),
      ('industrial_structure', s.category in ('factory','port','fishing_port','warehouse')),
      ('slope_stairs',         s.category = 'slope_street')
    ) as r(tag_code, ok)
   where s.data_status <> 'archived' and coalesce(r.ok, false)
     and exists (select 1 from tags t where t.code = r.tag_code);

  delete from spot_tags st where st.origin = 'rule'
     and not exists (select 1 from _rt where _rt.spot_id = st.spot_id and _rt.tag_code = st.tag_code);
  get diagnostics v_removed = row_count;
  insert into spot_tags (spot_id, tag_code, origin, score)
    select spot_id, tag_code, 'rule', 1.0 from _rt on conflict (spot_id, tag_code) do nothing;
  get diagnostics v_added = row_count;
  select coalesce(jsonb_object_agg(tag_code, n), '{}') into v_by
    from (select tag_code, count(*) n from spot_tags where origin = 'rule' group by 1) x;
  return jsonb_build_object('added', v_added, 'removed', v_removed, 'by_tag', v_by);
end $$;
