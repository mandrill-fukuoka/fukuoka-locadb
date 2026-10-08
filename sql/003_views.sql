-- 確認作業用ビュー
create or replace view v_review_queue as
select s.code, s.name, sc.name category, s.status, s.confidence, s.summary,
       (select string_agg(t.name, '・') from spot_tags st join tags t on t.code=st.tag_code
         where st.spot_id=s.id and st.score >= 0.6) tags,
       exists(select 1 from change_risks r where r.spot_id=s.id and not r.verified) risk_unverified,
       'https://www.google.com/maps?q=' || ST_Y(s.geom::geometry) || ',' || ST_X(s.geom::geometry) map_url
from spots s left join spot_categories sc on sc.code=s.category
where s.status in ('ai_tagged','scouted')
order by s.code;

create or replace view v_clusters as
select c.code, c.travel_mode, c.total_minutes, c.transition_score, c.transition_label,
       string_agg(s.name, ' → ' order by cs.seq) route, c.status
from clusters c join cluster_spots cs on cs.cluster_id=c.id join spots s on s.id=cs.spot_id
group by c.id order by c.transition_score desc;

create or replace view v_risks_to_check as
select s.code, s.name, r.risk_type, r.evidence, r.source_url, r.detected_at, r.review_by
from change_risks r join spots s on s.id=r.spot_id
where not r.verified order by r.review_by;
