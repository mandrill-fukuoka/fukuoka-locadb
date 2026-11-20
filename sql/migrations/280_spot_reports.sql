-- ============================================================
-- 280: 誤りの連絡口（順5。2026-11-20。番号帯 280〜289）
--  地点ページの「この情報は違う」から届く連絡を受ける表。
--  - 連絡者の名前・メールアドレス・IP・端末の情報は取らない（記録の取り扱いと同じ方針）。返事はしない
--  - kind：wrong_place（位置・住所が違う）／wrong_name（名前・呼び方が違う）／closed（なくなった・使えなくなった）／
--          wrong_info（説明・事実が違う）／photo（写真が違う・外してほしい）／other
--  - 人が見て、直したら done、取り合わなければ dismissed にする（行は消さない）
--  - 費用はかけない（AIは使わない）。受け付けは Webアプリ側で回数を制限する
-- ============================================================
create table if not exists spot_reports (
  id          bigserial primary key,
  spot_id     bigint references spots(id) on delete set null,
  spot_name   text,                                   -- 連絡を受けた時点の名前（地点が消えても分かるように）
  kind        text not null check (kind in ('wrong_place','wrong_name','closed','wrong_info','photo','other')),
  note        text not null check (length(btrim(note)) between 1 and 1000),
  status      text not null default 'open' check (status in ('open','done','dismissed')),
  created_at  timestamptz not null default now(),
  handled_by  text,
  handled_at  timestamptz,
  handled_note text
);
create index if not exists spot_reports_status_idx on spot_reports (status, created_at desc);
create index if not exists spot_reports_spot_idx on spot_reports (spot_id) where spot_id is not null;
