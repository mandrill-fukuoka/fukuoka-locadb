-- 重要度の点数（順2、DB変更260。tools/migrate.py が毎回作り直す）
--  refresh_spot_importance() が spot_importance を全件作り直す（掲載中で名前のある地点だけ）。
--  点数＝次の7つの材料（各0〜1）の重み付き和（重みの合計は1）。重みは評価の物差し（tools/yardstick.py）で後退しない範囲で調整する
--    wiki_views 0.25  Wikipedia の月間閲覧数（10万で1。対数）
--    sitelinks  0.15  Wikidata の言語版の数（40で1）
--    photos     0.15  採用済みの写真（0.5）＋周辺の Commons 写真の枚数（10枚で0.5）
--    ffc        0.15  FFC 登録
--    osm        0.10  OSM のタグの充実（wikidata・wikipedia・website・heritage・tourism・historic・name:en・opening_hours・description の5つで1）
--    extent     0.10  敷地・長さ（2km で1。対数）
--    facts      0.10  承認済みの事実カード（3件で1）
--  穴場度＝材料の豊富さ（photos・osm・extent・facts）×（1−知名度（wiki_views・sitelinks））。材料が全くなければ0
do $$
declare f record;
begin
  for f in select p.oid::regprocedure as sig from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname in ('refresh_spot_importance','revoke_facts_by_source')
  loop execute format('drop function %s', f.sig); end loop;
end $$;

create function refresh_spot_importance() returns int language plpgsql as $$
declare n int;
begin
  delete from spot_importance;
  insert into spot_importance (spot_id, score, hidden_score, parts)
  select p.spot_id,
         round((0.25 * p.wiki_views + 0.15 * p.sitelinks + 0.15 * p.photos + 0.15 * p.ffc + 0.10 * p.osm
                + 0.10 * p.extent + 0.10 * p.facts)::numeric, 4)::real,
         round((case when q.quality > 0 then q.quality * (1 - q.pop) else 0 end)::numeric, 4)::real,
         jsonb_build_object('wiki_views', round(p.wiki_views::numeric, 3), 'sitelinks', round(p.sitelinks::numeric, 3),
                            'photos', round(p.photos::numeric, 3), 'ffc', p.ffc, 'osm', round(p.osm::numeric, 3),
                            'extent', round(p.extent::numeric, 3), 'facts', round(p.facts::numeric, 3))
    from (
      select s.id as spot_id,
             coalesce((select least(ln(1 + v.value) / ln(100001), 1) from spot_signals v
                        where v.spot_id = s.id and v.kind = 'wikipedia_views'), 0)::float8 as wiki_views,
             coalesce((select least(v.value / 40.0, 1) from spot_signals v
                        where v.spot_id = s.id and v.kind = 'wikidata_sitelinks'), 0)::float8 as sitelinks,
             (case when exists (select 1 from spot_photos ph where ph.spot_id = s.id) then 0.5 else 0 end
              + 0.5 * least((select count(*) from spot_photo_candidates c where c.spot_id = s.id) / 10.0, 1))::float8 as photos,
             (case when s.ffc_registered then 1 else 0 end)::float8 as ffc,
             coalesce(least((select count(*) from jsonb_object_keys(coalesce(ft.tags, '{}'::jsonb)) k
                              where k in ('wikidata','wikipedia','website','contact:website','heritage','tourism','historic','name:en','opening_hours','description')) / 5.0, 1), 0)::float8 as osm,
             (case when ft.extent_m is null then 0 else least(ln(1 + ft.extent_m) / ln(2001), 1) end)::float8 as extent,   -- least は null を無視するので、結べていない地点は先に0にする
             least((select count(*) from spot_facts f where f.spot_id = s.id and f.status = 'approved') / 3.0, 1)::float8 as facts
        from spots s
        left join lateral (select f.tags, f.extent_m from spot_features sf join features f on f.key = sf.feature_key
                            where sf.spot_id = s.id order by (sf.role = 'primary') desc, sf.feature_key limit 1) ft on true
       where s.data_status <> 'archived' and s.name not like '（無名）%'
    ) p
    cross join lateral (select (0.15 * p.photos + 0.10 * p.osm + 0.10 * p.extent + 0.10 * p.facts) / 0.45 as quality,
                               (0.25 * p.wiki_views + 0.15 * p.sitelinks) / 0.40 as pop) q;
  get diagnostics n = row_count;
  return n;
end $$;

-- 出どころごとに事実を取り消す（例：Wikidata から入れた事実をまとめて取り消す）。行は残し、履歴にも記録する
create function revoke_facts_by_source(p_kind text, p_user text, p_note text default null) returns int language plpgsql as $$
declare n int := 0; r record;
begin
  if nullif(btrim(coalesce(p_user, '')), '') is null then raise exception 'p_user（取り消す人）が必要'; end if;
  for r in select id from spot_facts where source_kind = p_kind and status = 'approved' order by id loop
    perform fact_revoke(r.id, p_user, p_note);
    n := n + 1;
  end loop;
  return n;
end $$;
