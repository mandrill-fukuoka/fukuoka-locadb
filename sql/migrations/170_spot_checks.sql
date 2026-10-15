-- ============================================================
-- 170: 登録地の自動チェックの指摘（タスクG。2026-10-15 利用者と合意）
--  locadb/verify.py（Actions「verify」の apply）が書く。AIは使わず、指摘だけを残す（地点や下書きは変えない）
--  - fkey：同じ指摘を毎回同じ行に当てるための鍵（チェック・下書き・内容）
--  - status：open（未対応）/ resolved（次の実行で見つからなくなった。自動）/ dismissed（人が「問題なし」とした）
--    dismissed は、同じ指摘が出続けても開き直さない。resolved は、また見つかれば open に戻す
-- ============================================================
create table if not exists spot_checks (
  id          bigserial primary key,
  spot_id     bigint not null references spots(id) on delete cascade,
  check_code  text not null,
  target      text not null check (target in ('category','address','summary')),
  severity    text not null check (severity in ('warn','info')),
  label       text not null,                        -- チェックの表示名（verify.py の CHECKS）
  detail      text not null,
  note_id     bigint references spot_ai_notes(id) on delete cascade,
  fkey        text not null,
  status      text not null default 'open' check (status in ('open','resolved','dismissed')),
  first_seen  timestamptz not null default now(),
  last_seen   timestamptz not null default now(),
  last_job    bigint references job_runs(id),
  closed_by   text,
  closed_at   timestamptz,
  close_note  text,
  unique (spot_id, fkey)
);
create index if not exists spot_checks_open_idx on spot_checks (status, target, check_code) where status = 'open';
create index if not exists spot_checks_note_idx on spot_checks (note_id) where note_id is not null;
