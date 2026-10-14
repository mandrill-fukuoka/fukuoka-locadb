-- ============================================================
-- 131: 2026-10-14 15時台の LAYA の一括処理（版 laya-summary-v2）を取り消す
--  - どの地点にも同じタグ nostalgic（懐かしい）を付けていた → LAYA のタグ（origin='ai', source='laya'）をすべて消す
--    （LAYA がタグを書いたのはこの処理が初めて）
--  - 説明文の下書きを「〇〇周辺は、<景観コード>の特性を持つエリア…」という定型文に置き換えていた
--    → v2 の下書きは却下（rejected）にし、置き換えられていた直前の下書きを採用待ち（draft）に戻す
--  件数は migrate の出力に出る。戻した後、1つの地点に LAYA の採用待ちの下書きが2つ以上あれば止める
-- ============================================================
do $$
declare n_tags int; n_v2 int; n_restored int; n_dup int;
begin
  delete from spot_tags where origin = 'ai' and source = 'laya';
  get diagnostics n_tags = row_count;

  -- v2 で置き換えられた地点（v2 の下書きがある地点）を控える
  create temp table v2_spots on commit drop as
    select distinct spot_id from spot_ai_notes
     where source = 'laya' and kind = 'summary' and prompt_version = 'laya-summary-v2';

  update spot_ai_notes set status = 'rejected', reviewed_by = 'migration', reviewed_at = now(),
         review_note = '定型文のため取り消し（2026-10-14 DB変更131）'
   where source = 'laya' and kind = 'summary' and prompt_version = 'laya-summary-v2' and status in ('draft', 'superseded');
  get diagnostics n_v2 = row_count;

  -- その地点に LAYA の採用待ちの下書きが残っていなければ、v2 より前で最も新しい下書きを採用待ちに戻す
  update spot_ai_notes n set status = 'draft'
    from (select distinct on (a.spot_id) a.id
            from spot_ai_notes a join v2_spots v on v.spot_id = a.spot_id
           where a.source = 'laya' and a.kind = 'summary' and a.status = 'superseded'
             and a.prompt_version is distinct from 'laya-summary-v2'
             and not exists (select 1 from spot_ai_notes d where d.spot_id = a.spot_id and d.source = 'laya'
                                                            and d.kind = 'summary' and d.status = 'draft')
           order by a.spot_id, a.created_at desc, a.id desc) p
   where n.id = p.id;
  get diagnostics n_restored = row_count;

  select count(*) into n_dup from (select spot_id from spot_ai_notes where source = 'laya' and kind = 'summary'
                                     and status = 'draft' group by spot_id having count(*) > 1) x;
  raise notice 'LAYA のタグを消した件数: % / v2 の下書きを却下した件数: % / 元の下書きに戻した件数: %', n_tags, n_v2, n_restored;
  if n_dup > 0 then
    raise exception 'LAYA の採用待ちの下書きが2つ以上ある地点が % 件あるため止めました', n_dup;
  end if;
end $$;
