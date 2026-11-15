import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLES = {"appointments", "repair_orders", "tows", "messages", "audit_events"}


def migration_config():
    from alembic.config import Config

    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/migrations"))
    return config


def assert_metadata_matches(engine):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from bear.db import metadata

    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), metadata) == []


def test_migration_lifecycle(tmp_path, monkeypatch):
    from alembic import command
    from sqlalchemy import inspect

    from bear.db import make_engine

    url = f"sqlite:///{tmp_path}/migration.db"
    monkeypatch.setenv("DATABASE_URL", url)
    config = migration_config()
    engine = make_engine(url)
    try:
        command.upgrade(config, "head")
        assert set(inspect(engine).get_table_names()) == TABLES | {"alembic_version"}
        assert_metadata_matches(engine)
        command.downgrade(config, "base")
        assert not TABLES.intersection(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_migration_cli_from_backend(tmp_path):
    from sqlalchemy import inspect

    from bear.db import make_engine

    url = f"sqlite:///{tmp_path}/cli.db"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT / "backend",
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    engine = make_engine(url)
    try:
        assert set(inspect(engine).get_table_names()) == TABLES | {"alembic_version"}
        assert_metadata_matches(engine)
    finally:
        engine.dispose()
