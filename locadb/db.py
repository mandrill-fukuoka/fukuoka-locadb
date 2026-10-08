"""DB接続と共通ヘルパー"""
import json
import os
from contextlib import contextmanager

import psycopg
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from psycopg.rows import dict_row


def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL が未設定です（.env を確認）")
    return url


@contextmanager
def connect():
    with psycopg.connect(dsn(), row_factory=dict_row) as conn:
        yield conn


@contextmanager
def job(conn, name: str):
    """job_runs に実行記録を残す"""
    row = conn.execute("insert into job_runs (job) values (%s) returning id", (name,)).fetchone()
    conn.commit()
    stats: dict = {}
    try:
        yield stats
        conn.execute(
            "update job_runs set finished_at=now(), stats=%s where id=%s",
            (json.dumps(stats, ensure_ascii=False), row["id"]),
        )
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        conn.execute(
            "update job_runs set finished_at=now(), error=%s, stats=%s where id=%s",
            (repr(e), json.dumps(stats, ensure_ascii=False), row["id"]),
        )
        conn.commit()
        raise
    conn.commit()
