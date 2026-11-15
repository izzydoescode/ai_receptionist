"""Opt-in destructive contract against a dedicated disposable Postgres database."""

import os
from datetime import timedelta

import pytest

from test_migrations import assert_metadata_matches, migration_config
from test_store import APPOINTMENT, MESSAGE, SHOP, SLOT, assert_concurrent_booking, audit_rows

pytestmark = pytest.mark.pg
URL = os.environ.get("BEAR_TEST_PG_URL")
if not URL:
    pytest.skip("BEAR_TEST_PG_URL is not set", allow_module_level=True)


@pytest.fixture(scope="module")
def pg_engine():
    from alembic import command

    from bear.db import make_engine

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", URL)
        config = migration_config()
        engine = make_engine(URL)
        try:
            command.downgrade(config, "base")
            command.upgrade(config, "head")
            yield engine
        finally:
            engine.dispose()
            command.downgrade(config, "base")


@pytest.fixture
def pg_store(pg_engine):
    from bear.store import Store

    store = Store(pg_engine)
    store.reset_shop(SHOP)
    store.reset_shop("other")
    return store


def test_pg_metadata(pg_engine):
    assert_metadata_matches(pg_engine)


def test_pg_utc_roundtrip(pg_store):
    pg_store.book_appointment(SHOP, SLOT, 2, **APPOINTMENT)
    row = pg_store.board(SHOP)["appointments"][0]
    assert row["slot_start"] == SLOT
    assert row["slot_start"].utcoffset() == timedelta(0)
    assert row["created_at"].utcoffset() == timedelta(0)


def test_pg_concurrency(pg_store):
    assert_concurrent_booking(pg_store)


def test_pg_shop_isolation(pg_store):
    pg_store.create_message(SHOP, **MESSAGE)
    other = pg_store.create_message("other", **MESSAGE)
    assert pg_store.board("other")["messages"] == [other]
    pg_store.reset_shop(SHOP)
    assert pg_store.board(SHOP)["messages"] == []
    assert pg_store.board("other")["messages"] == [other]


def test_pg_write_guard_rollback(pg_store, pg_engine):
    from bear.store import Store

    def guard():
        raise RuntimeError("deadline")

    before = audit_rows(pg_engine)
    with pytest.raises(RuntimeError, match="deadline"):
        Store(pg_engine, write_guard=guard).create_message(SHOP, **MESSAGE)
    assert pg_store.board(SHOP)["messages"] == []
    assert audit_rows(pg_engine) == before
