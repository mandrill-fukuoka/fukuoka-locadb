-- ============================================================
-- 291: 周りの建物の高さ（順6の後半。2026-11-22。番号帯 290〜299）
--  PLATEAU（福岡市の3D都市モデル。商用可・出典表示）の建築物の実測の高さから、地点ごとに周りの建物の高さの目安を保存する。
--  建物そのもの（位置・形）は保存しない（地点ごとの数だけ）。取り込みは locadb/plateau_heights.py（年1回ほど。手動）。
--  - h_max_100／h_mean_100：半径100m内の建物の高さの最大・平均（m）
--  - tall_300：半径300m内の、高さ45m以上（おおむね15階建て以上）の建物の数
--  - horizon_mean_deg／horizon_max_deg：地上1.6mから半径300m内を16方位に見たときの、建物の見上げ角（度）の平均・最大。
--    大きいほど周りが建物でふさがれている（日陰・谷間の目安）
--  費用はかけない（AIは使わない）。撮影の可否は書かない
-- ============================================================
create table if not exists spot_skyline (
  spot_id          bigint primary key references spots(id) on delete cascade,
  h_max_100        real,
  h_mean_100       real,
  tall_300         integer not null default 0,
  horizon_mean_deg real,
  horizon_max_deg  real,
  source           text not null,                      -- 例：plateau-40130-fukuoka-shi-2024
  fetched_at       timestamptz not null default now()
);
