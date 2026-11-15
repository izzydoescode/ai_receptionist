import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def seeded_store(tmp_path, monkeypatch):
    from bear.db import create_schema, make_engine
    from bear.store import Store

    url = f"sqlite:///{tmp_path}/seed.db"
    monkeypatch.setenv("DATABASE_URL", url)
    engine = make_engine(url)
    create_schema(engine)
    yield Store(engine), url
    engine.dispose()


def test_demo_seed_idempotent_and_isolated(seeded_store):
    from bear.seed import main

    store, _ = seeded_store
    assert main(["--demo"]) == 0
    before = store.list_repair_orders("vice-city-auto")
    assert {r["ro_number"] for r in before} == {"1001", "1002", "1003", "1004"}
    assert main(["--demo"]) == 0
    assert {r["public_id"] for r in store.list_repair_orders("vice-city-auto")} == {r["public_id"] for r in before}
    assert main(["--demo", "--shop", "other-shop"]) == 0
    assert len(store.list_repair_orders("other-shop")) == 4
    assert {r["public_id"] for r in store.list_repair_orders("vice-city-auto")} == {r["public_id"] for r in before}


def test_demo_flag_required():
    from bear.seed import main

    with pytest.raises(SystemExit) as error:
        main([])
    assert error.value.code == 2


def test_seed_requires_schema(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "bear.seed", "--demo"],
        cwd=ROOT / "backend",
        env={**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path}/missing.db"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "alembic upgrade head" in result.stdout + result.stderr


def test_seed_cli_is_private(seeded_store):
    store, url = seeded_store
    result = subprocess.run(
        [sys.executable, "-m", "bear.seed", "--demo"],
        cwd=ROOT / "backend",
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    records = store.list_repair_orders("vice-city-auto")
    assert len(records) == 4
    assert len(result.stdout.strip().splitlines()) == 1
    assert "4" in result.stdout
    for row in records:
        for value in (row["customer_name"], row["phone"], row["phone"].removeprefix("+1")):
            assert value not in result.stdout + result.stderr
