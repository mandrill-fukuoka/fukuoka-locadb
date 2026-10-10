"""テスト用：手元の PostgreSQL（PostGIS）に、テストごとの使い捨てDBを作る

  TEST_PG=postgresql://app:app@localhost:5433 pytest -q
OSMのデータは tests/osmworld.py で小さな架空の地図を作り、.osm ファイルとして読ませる。
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
PG = os.environ.get("TEST_PG", "postgresql://app:app@localhost:5433")
TEMPLATE = "locadb_test_tmpl"

TEST_AREAS = {"fuk-chuo": ("中央区", 1), "fuk-hakata": ("博多区", 2)}


def _admin():
    return psycopg.connect(PG + "/postgres", autocommit=True)


@pytest.fixture(scope="session")
def template_db():
    with _admin() as a:
        a.execute(f"drop database if exists {TEMPLATE}")
        a.execute(f"create database {TEMPLATE}")
    env = dict(os.environ, DATABASE_URL=f"{PG}/{TEMPLATE}")
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "migrate.py")], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    yield TEMPLATE


@pytest.fixture
def db(template_db, monkeypatch):
    name = "t_" + uuid.uuid4().hex[:10]
    with _admin() as a:
        a.execute(f"create database {name} template {template_db}")
    url = f"{PG}/{name}"
    monkeypatch.setenv("DATABASE_URL", url)
    from locadb import build_clusters, osm_source, sync
    monkeypatch.setattr(osm_source, "AREAS", TEST_AREAS)
    monkeypatch.setattr(sync, "AREAS", TEST_AREAS)
    monkeypatch.setattr(build_clusters, "AREAS", TEST_AREAS)
    conn = psycopg.connect(url, row_factory=dict_row)
    conn.execute("delete from areas")
    conn.commit()
    yield conn
    conn.close()
    with _admin() as a:
        a.execute(f"drop database if exists {name} with (force)")
