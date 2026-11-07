-- ============================================================
-- 230: 近くのAED（2026-11-07。番号帯 M＝230〜239。利用者と合意）
--  aed_points：福岡市「AED設置施設登録制度」に登録された施設のAED設置場所（BODIK ODCS「福岡市 AED」、CC BY 4.0）。
--    locadb/bodik_aed.py（Actions「aed-import」）で入れる。電話番号・担当者などの連絡先の列は読まない。
--    取り込みのたびに、一覧にないもの（登録の取り下げ）は active=false にして画面から消す。
--  地点ページに、半径300m以内の登録AEDを近い順に最大3件出す（関数 aed_view は sql/api/10_spots.sql）。
--  登録時点の情報で、使えるかどうか・使える時間は現地で確かめる必要がある（画面に明記）。撮影の可否・安全は保証しない。
-- ============================================================
create table if not exists aed_points (
  id          serial primary key,
  source_key  text not null unique,                    -- bodik:aed:<名前と住所のハッシュ>
  name        text not null,                           -- 設置施設の名称
  address     text,
  place_note  text,                                    -- 設置位置（原文）
  hours_note  text,                                    -- 利用可能な曜日・時間など（原文）
  geom        geography(Point, 4326) not null,
  geocoded    boolean not null default false,          -- 座標が住所検索による目安のとき true
  active      boolean not null default true,
  source_url  text not null,
  source_title text not null,
  license     text not null default 'CC BY 4.0',
  fetched_at  timestamptz not null default now()
);
create index if not exists aed_points_geom_idx on aed_points using gist (geom);
