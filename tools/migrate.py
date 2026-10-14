"""データベースの変更を順番に適用する（GitHub Actions「db-migrate」から実行）

  python tools/migrate.py --status      # 適用済み・未適用の一覧
  python tools/migrate.py --dry-run     # 全部を1つのトランザクションで実行し、最後に取り消す（試し）
  python tools/migrate.py               # 本番に適用

- sql/migrations/NNN_*.sql … 表の作成・変更。1回だけ実行し、schema_migrations に記録する
- sql/api/*.sql            … 関数（画面から呼ぶRPC）。毎回すべて作り直す（何度実行しても同じ結果）
- sql/grants.sql           … 権限・RLS。毎回最後に実行する
適用済みのファイルが後から書き換えられていたら、止めて知らせる（同じ番号を使い回さない）。
"""
import argparse
import hashlib
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent / "sql"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    url = os.environ.get("DATABASE_URL") or sys.exit("DATABASE_URL が未設定です")

    with psycopg.connect(url, autocommit=False) as conn:
        conn.add_notice_handler(lambda d: print("  ・" + d.message_primary))   # DB変更が出す件数などを表示
        conn.execute("""create table if not exists public.schema_migrations (
                          version text primary key, checksum text not null,
                          applied_at timestamptz not null default now())""")
        conn.execute("alter table public.schema_migrations enable row level security")
        conn.commit()
        done = {v: c for v, c in conn.execute("select version, checksum from public.schema_migrations")}
        files = sorted(ROOT.glob("migrations/*.sql"))
        pending = [f for f in files if f.name not in done]
        changed = [f.name for f in files if f.name in done and done[f.name] != sha(f)]
        if changed:
            print(f"！ 適用済みのファイルが書き換えられています: {changed}（新しい番号のファイルで変更してください）")
            return 2
        for f in files:
            print(("  済 " if f.name in done else "  未 ") + f.name)
        if a.status:
            return 0

        try:
            for f in pending:
                print(f"→ 適用 {f.name}")
                conn.execute(f.read_text(encoding="utf-8"))
                conn.execute("insert into public.schema_migrations (version, checksum) values (%s,%s)",
                             (f.name, sha(f)))
            for f in sorted(ROOT.glob("api/*.sql")):
                print(f"→ 関数 {f.name}")
                conn.execute(f.read_text(encoding="utf-8"))
            g = ROOT / "grants.sql"
            if g.exists():
                print("→ 権限 grants.sql")
                conn.execute(g.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            detail = getattr(getattr(e, "diag", None), "message_detail", None)
            hint = getattr(getattr(e, "diag", None), "message_hint", None)
            print(f"！ 失敗したため、すべて取り消しました: {e}" + (f"\n  詳細: {detail}" if detail else "")
                  + (f"\n  ヒント: {hint}" if hint else ""))
            return 1
        if a.dry_run:
            conn.rollback()
            print("試し実行のため、すべて取り消しました（本番は変わっていません）")
        else:
            conn.commit()
            print("適用しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
