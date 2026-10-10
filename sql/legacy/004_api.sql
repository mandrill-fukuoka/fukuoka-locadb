-- Web画面（ヘテムル上のPHP）から Supabase REST(RPC) 経由で呼ぶ関数
-- PHPはサーバー側で service_role キーを使う。ブラウザにキーは出さない。

-- 景観順序の一致率
create or replace function seq_match(p_seq text[], p_groups text[]) returns real
language plpgsql immutable as $$
declare i int := 1; g text;
begin
  if p_seq is null or cardinality(p_seq) = 0 then return 0; end if;
  foreach g in array p_groups loop
    if i <= cardinality(p_seq) and g = p_seq[i] then i := i + 1; end if;
  end loop;
  return (i - 1)::real / cardinality(p_seq);
end $$;

-- シーン検索：AIが変換した条件（景観順序・タグ重み）でクラスターを順位付け
create or replace function search_clusters(
  p_seq text[], p_tags jsonb, p_mode text default 'any',
  p_area text default null, p_limit int default 10
) returns jsonb language sql stable as $$
with c as (
  select c.id, c.code, c.name, c.transition_label, c.transition_score, c.total_minutes,
         c.travel_mode, c.status, c.area_code
  from clusters c
  where c.status <> 'archived'
    and (p_area is null or c.area_code = p_area)
    and (p_mode is null or p_mode = 'any' or c.travel_mode = p_mode)
),
sp as (
  select cs.cluster_id, cs.seq, cs.minutes_from_prev, s.id spot_id, s.code, s.name, s.confidence,
         s.summary, sc.landscape_group grp, lg.name grp_name,
         ST_Y(s.geom::geometry) lat, ST_X(s.geom::geometry) lon,
         exists(select 1 from change_risks r where r.spot_id=s.id and r.risk_type<>'none') has_risk,
         coalesce((select sum((p_tags->>t.tag_code)::real * t.score)
                   from spot_tags t where t.spot_id=s.id and p_tags ? t.tag_code), 0) tag_hit
  from cluster_spots cs
  join c on c.id = cs.cluster_id
  join spots s on s.id = cs.spot_id
  join spot_categories sc on sc.code = s.category
  join landscape_groups lg on lg.code = sc.landscape_group
),
agg as (
  select c.*,
         seq_match(p_seq, array_agg(sp.grp order by sp.seq)) seq_score,
         least(sum(sp.tag_hit) / greatest((select sum(value::real) from jsonb_each_text(p_tags)), 0.0001), 1) tag_score,
         jsonb_agg(jsonb_build_object(
           'code', sp.code, 'name', sp.name, 'group', sp.grp_name, 'lat', sp.lat, 'lon', sp.lon,
           'minutes_from_prev', sp.minutes_from_prev, 'confidence', sp.confidence,
           'summary', sp.summary, 'risk', sp.has_risk) order by sp.seq) spots
  from c join sp on sp.cluster_id = c.id
  group by c.id, c.code, c.name, c.transition_label, c.transition_score, c.total_minutes,
           c.travel_mode, c.status, c.area_code
)
select coalesce(jsonb_agg(r order by r.match desc), '[]'::jsonb)
from (
  select a.code, a.name, a.transition_label, a.transition_score, a.total_minutes, a.travel_mode,
         a.status, a.area_code, a.spots,
         round((0.45 * a.seq_score + 0.40 * a.tag_score
                + 0.15 * least(coalesce(a.transition_score,0) / 3, 1))::numeric, 3) match
  from agg a order by match desc limit p_limit
) r;
$$;

-- 確認待ち地点（ページング）
create or replace function review_queue(p_area text default null, p_status text default null,
                                        p_q text default null, p_offset int default 0, p_limit int default 50)
returns jsonb language sql stable as $$
select coalesce(jsonb_agg(x), '[]'::jsonb) from (
  select s.id, s.code, s.name, s.area_code, sc.name category, s.era_feel, s.shoot_scope,
         s.status, s.confidence, s.summary,
         ST_Y(s.geom::geometry) lat, ST_X(s.geom::geometry) lon,
         (select jsonb_agg(jsonb_build_object('code', t.code, 'name', t.name, 'score', st.score, 'origin', st.origin)
                           order by st.score desc)
            from spot_tags st join tags t on t.code=st.tag_code where st.spot_id=s.id) tags,
         (select jsonb_agg(jsonb_build_object('id', r.id, 'type', r.risk_type, 'verified', r.verified))
            from change_risks r where r.spot_id=s.id) risks
  from spots s left join spot_categories sc on sc.code=s.category
  where (p_area is null or s.area_code=p_area)
    and (p_status is null or s.status=p_status)
    and (p_q is null or s.name ilike '%'||p_q||'%')
    and s.status <> 'archived'
  order by s.code offset p_offset limit p_limit
) x;
$$;

-- 地点の確認結果を保存（人の判断。タグは origin='human' で保存しAI再実行で上書きされない）
create or replace function review_spot(p_id bigint, p_status text, p_confidence text,
                                       p_summary text, p_tags text[], p_reviewer text)
returns void language plpgsql as $$
begin
  update spots set status=p_status, confidence=p_confidence,
                   summary=coalesce(p_summary, summary)
  where id=p_id;
  if p_tags is not null then
    delete from spot_tags where spot_id=p_id;
    insert into spot_tags (spot_id, tag_code, origin, score)
      select p_id, t, 'human', 1.0 from unnest(p_tags) t
      where exists(select 1 from tags where code=t);
  end if;
  insert into sources (spot_id, kind, title, note)
    values (p_id, 'review', 'Web確認', coalesce(p_reviewer,'') || ': ' || p_status || '/' || p_confidence);
end $$;

-- 変化リスクの確認
create or replace function review_risk(p_id bigint, p_verified boolean, p_dismiss boolean, p_note text)
returns void language plpgsql as $$
begin
  if p_dismiss then
    update change_risks set risk_type='none', verified=true,
           evidence = evidence || E'\n[却下] ' || coalesce(p_note,'') where id=p_id;
  else
    update change_risks set verified=p_verified,
           evidence = evidence || E'\n[確認] ' || coalesce(p_note,'') where id=p_id;
  end if;
end $$;

-- 一時停止防止・死活確認用
create or replace function ping() returns jsonb language sql stable as $$
  select jsonb_build_object('spots', (select count(*) from spots),
                            'clusters', (select count(*) from clusters), 'at', now());
$$;

-- 匿名キーからは一切アクセスさせない（PHPは service_role キーのみ使用）
do $$ begin
  if exists (select 1 from pg_roles where rolname='anon') then
    revoke all on all tables in schema public from anon;
    revoke execute on function search_clusters(text[],jsonb,text,text,int), review_queue(text,text,text,int,int),
      review_spot(bigint,text,text,text,text[],text), review_risk(bigint,boolean,boolean,text), ping()
      from anon;
  end if;
end $$;
revoke execute on function search_clusters(text[],jsonb,text,text,int), review_queue(text,text,text,int,int),
  review_spot(bigint,text,text,text,text[],text), review_risk(bigint,boolean,boolean,text), ping()
  from public;
do $$ begin
  if exists (select 1 from pg_roles where rolname='service_role') then
    grant execute on function search_clusters(text[],jsonb,text,text,int), review_queue(text,text,text,int,int),
      review_spot(bigint,text,text,text,text[],text), review_risk(bigint,boolean,boolean,text), ping()
      to service_role;
    -- 「Automatically expose new tables」をオフにした場合でも動くよう明示的に付与
    grant usage on schema public to service_role;
    grant select, insert, update, delete on all tables in schema public to service_role;
    grant usage, select on all sequences in schema public to service_role;
  end if;
end $$;
