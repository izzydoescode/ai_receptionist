"""Behaviour shared by SQLite and the optional Postgres contract."""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from zoneinfo import ZoneInfo

import pytest

SHOP = "vice-city-auto"
SLOT = datetime(2026, 11, 16, 10, tzinfo=ZoneInfo("America/New_York"))
MESSAGE = dict(
    caller_name="Zelda Quixote",
    phone="+13055550199",
    reason="Private callback",
    urgency="normal",
    callback_language="en",
)
APPOINTMENT = dict(
    customer_name="Zelda Quixote",
    phone="+13055550199",
    vehicle="Demo sedan",
    service="Private service",
    language="en",
    notes=None,
)
TOW = dict(
    customer_name="Zelda Quixote",
    phone="+13055550199",
    pickup_location="Private street",
    vehicle="Demo sedan",
    issue="Private issue",
    is_safe=True,
    destination="Demo shop",
    language="en",
    status="requested",
)
REPAIR = dict(
    ro_number="9001",
    customer_name="Zelda Quixote",
    phone="+13055550199",
    plate="DEMO",
    vehicle="Demo sedan",
    status="ready",
    note="Private note",
    eta="Tomorrow",
    balance_estimate="$10",
)
TABLES = {"appointments", "repair_orders", "tows", "messages", "audit_events"}


@pytest.fixture
def engine(tmp_path):
    from bear.db import create_schema, make_engine

    engine = make_engine(f"sqlite:///{tmp_path}/store.db")
    create_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def store(engine):
    from bear.store import Store

    return Store(engine)


def audit_rows(engine):
    from sqlalchemy import select

    from bear.db import metadata

    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(select(metadata.tables["audit_events"])).mappings()]


def test_schema_contract(engine):
    from sqlalchemy import inspect

    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == TABLES
    extras = {
        "appointments": set(APPOINTMENT) | {"slot_start", "bay"},
        "repair_orders": set(REPAIR) | {"updated_at"},
        "tows": set(TOW),
        "messages": set(MESSAGE) | {"vehicle", "conversation_id"},
        "audit_events": {"event", "entity_public_id", "outcome"},
    }
    for table in TABLES:
        columns = {c["name"]: c for c in inspector.get_columns(table)}
        assert set(columns) == {"id", "shop_id", "public_id", "created_at"} | extras[table]
        assert inspector.get_pk_constraint(table)["constrained_columns"] == ["id"]
        for name in ("shop_id", "public_id", "created_at"):
            assert columns[name]["nullable"] is False
        assert columns["shop_id"]["type"].length == 64
        assert columns["public_id"]["type"].length == 7
        assert any(i["column_names"] == ["shop_id"] for i in inspector.get_indexes(table))
        assert any(u["column_names"] == ["public_id"] for u in inspector.get_unique_constraints(table))
    assert any(u["column_names"] == ["shop_id", "ro_number"] for u in inspector.get_unique_constraints("repair_orders"))
    assert any(
        u["name"] == "uq_appointments_shop_slot_bay" and u["column_names"] == ["shop_id", "slot_start", "bay"]
        for u in inspector.get_unique_constraints("appointments")
    )


def test_public_ids_and_utc_roundtrip(store, engine):
    store.book_appointment(SHOP, SLOT, 2, **APPOINTMENT)
    store.create_tow(SHOP, **TOW)
    store.create_message(SHOP, **MESSAGE)
    store.upsert_repair_order(SHOP, **REPAIR)
    board = store.board(SHOP)
    for table, prefix in [("appointments", "A"), ("repair_orders", "R"), ("tows", "T"), ("messages", "M")]:
        row = board[table][0]
        assert re.fullmatch(prefix + r"[0-9A-F]{6}", row["public_id"])
        assert row["created_at"].utcoffset() == timedelta(0)
    assert board["appointments"][0]["slot_start"] == SLOT.astimezone(UTC)
    assert board["appointments"][0]["slot_start"].utcoffset() == timedelta(0)
    assert board["repair_orders"][0]["updated_at"].utcoffset() == timedelta(0)
    for row in audit_rows(engine):
        assert re.fullmatch(r"E[0-9A-F]{6}", row["public_id"])
        assert row["created_at"].utcoffset() == timedelta(0)


def test_lowest_bays_then_full(store):
    from bear.store import SlotFull

    assert store.book_appointment(SHOP, SLOT, 2, **APPOINTMENT)["bay"] == 1
    assert store.book_appointment(SHOP, SLOT, 2, **APPOINTMENT)["bay"] == 2
    with pytest.raises(SlotFull):
        store.book_appointment(SHOP, SLOT, 2, **APPOINTMENT)


def assert_concurrent_booking(store):
    from bear.store import SlotFull

    barrier = Barrier(10)

    def book(_):
        barrier.wait(timeout=10)
        try:
            return store.book_appointment(SHOP, SLOT, 2, **APPOINTMENT)
        except SlotFull:
            return "full"

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(book, range(10)))
    assert results.count("full") == 8
    assert sorted(r["bay"] for r in results if isinstance(r, dict)) == [1, 2]
    assert len(store.board(SHOP)["appointments"]) == 2


def test_concurrent_booking(store):
    assert_concurrent_booking(store)


def test_booked_counts_half_open_and_isolated(store):
    store.book_appointment(SHOP, SLOT, 2, **APPOINTMENT)
    store.book_appointment(SHOP, SLOT, 2, **APPOINTMENT)
    store.book_appointment(SHOP, SLOT + timedelta(hours=1), 2, **APPOINTMENT)
    store.book_appointment("other", SLOT, 2, **APPOINTMENT)
    assert store.booked_counts(SHOP, SLOT, SLOT + timedelta(hours=1)) == {SLOT.astimezone(UTC): 2}


def test_board_limits_order_and_shop_isolation(store):
    for shop in (SHOP, "other"):
        for i in range(3):
            store.create_message(shop, **{**MESSAGE, "reason": f"{shop}-{i}"})
            store.create_tow(shop, **{**TOW, "issue": f"{shop}-{i}"})
            store.book_appointment(shop, SLOT + timedelta(hours=i), 2, **APPOINTMENT)
            store.upsert_repair_order(shop, **{**REPAIR, "ro_number": str(i)})
    board = store.board(SHOP, 2)
    assert set(board) == TABLES - {"audit_events"}
    for table, rows in board.items():
        assert len(rows) == 2
        assert all(row["shop_id"] == SHOP for row in rows)
        key = "updated_at" if table == "repair_orders" else "created_at"
        assert [r[key] for r in rows] == sorted((r[key] for r in rows), reverse=True)
        all_rows = store.board(SHOP, 1000)[table]
        assert rows == all_rows[:2]
    assert [r["reason"] for r in board["messages"]] == [f"{SHOP}-2", f"{SHOP}-1"]


def test_reset_shop_isolated(store, engine):
    for shop in (SHOP, "other"):
        store.create_message(shop, **MESSAGE)
        store.create_tow(shop, **TOW)
        store.book_appointment(shop, SLOT, 2, **APPOINTMENT)
        store.upsert_repair_order(shop, **REPAIR)
    before = store.board("other")
    store.reset_shop(SHOP)
    assert all(not rows for rows in store.board(SHOP).values())
    assert store.board("other") == before
    assert all(row["shop_id"] == "other" for row in audit_rows(engine))


def test_upsert_is_idempotent(store):
    first = store.upsert_repair_order(SHOP, **REPAIR)
    second = store.upsert_repair_order(SHOP, **{**REPAIR, "status": "collected"})
    assert second["public_id"] == first["public_id"]
    assert store.list_repair_orders(SHOP) == [second]
    assert second["status"] == "collected"
    store.upsert_repair_order("other", **REPAIR)
    assert len(store.list_repair_orders(SHOP)) == 1


@pytest.mark.parametrize("operation", ["book", "tow", "message", "repair", "audit", "reset"])
def test_guard_rolls_back_every_write(engine, operation):
    from bear.store import Store

    baseline = Store(engine)
    baseline.create_message(SHOP, **MESSAGE)
    before, audit = baseline.board(SHOP), audit_rows(engine)

    def guard():
        raise RuntimeError("deadline")

    guarded = Store(engine, write_guard=guard)
    actions = {
        "book": lambda: guarded.book_appointment(SHOP, SLOT, 2, **APPOINTMENT),
        "tow": lambda: guarded.create_tow(SHOP, **TOW),
        "message": lambda: guarded.create_message(SHOP, **MESSAGE),
        "repair": lambda: guarded.upsert_repair_order(SHOP, **REPAIR),
        "audit": lambda: guarded.record_audit(SHOP, "repair_status.lookup", outcome="failed"),
        "reset": lambda: guarded.reset_shop(SHOP),
    }
    with pytest.raises(RuntimeError, match="deadline"):
        actions[operation]()
    assert baseline.board(SHOP) == before
    assert audit_rows(engine) == audit


def test_audit_events_contain_no_pii(store, engine):
    records = [
        store.book_appointment(SHOP, SLOT, 2, **APPOINTMENT),
        store.create_tow(SHOP, **TOW),
        store.create_message(SHOP, **MESSAGE),
    ]
    events = audit_rows(engine)
    assert {r["event"] for r in events} == {"appointment.booked", "tow.requested", "message.taken"}
    assert {r["entity_public_id"] for r in events} == {r["public_id"] for r in records}
    private = ["Zelda", "Quixote", "3055550199", "Private", "Demo sedan"]
    for row in events:
        assert set(row) == {"id", "shop_id", "public_id", "created_at", "event", "entity_public_id", "outcome"}
        assert all(value not in str(column) for column in row.values() for value in private)


@pytest.mark.parametrize("operation", ["book", "tow", "message", "audit"])
def test_public_id_collision_retries_with_new_id(store, engine, monkeypatch, operation):
    """A 6-hex public id collides by chance after a few thousand rows; callers must not see it."""
    first = {
        "book": lambda: store.book_appointment(SHOP, SLOT, 2, **APPOINTMENT),
        "tow": lambda: store.create_tow(SHOP, **TOW),
        "message": lambda: store.create_message(SHOP, **MESSAGE),
        "audit": lambda: store.record_audit(SHOP, "repair_status.lookup", outcome="failed"),
    }[operation]
    # First call uses ABC123 (+ ABC124 for its audit row); the second call's
    # first try collides with ABC123 and must retry with a fresh id.
    sequence = (
        ["ABC123", "ABC123", "DEF456"] if operation == "audit" else ["ABC123", "ABC124", "ABC123", "DEF456", "DEF457"]
    )
    ids = iter(sequence)
    monkeypatch.setattr("bear.store.secrets.token_hex", lambda n: next(ids))
    first()
    second = first()
    assert second["public_id"][1:] == "DEF456"
    if operation == "book":
        assert second["bay"] == 2
    assert len({row["public_id"] for row in audit_rows(engine)}) == len(audit_rows(engine))


def test_repair_public_id_collision_retries_with_new_id(store, monkeypatch):
    ids = iter(["ABC123", "ABC123", "DEF456"])
    monkeypatch.setattr("bear.store.secrets.token_hex", lambda n: next(ids))
    first = store.upsert_repair_order(SHOP, **REPAIR)
    second = store.upsert_repair_order(SHOP, **{**REPAIR, "ro_number": "9002"})
    assert first["public_id"] == "RABC123"
    assert second["public_id"] == "RDEF456"
    assert {row["ro_number"] for row in store.list_repair_orders(SHOP)} == {"9001", "9002"}


def test_write_clears_sqlite_begin_on_pooled_connections(store, engine):
    store.create_message(SHOP, **MESSAGE)
    connections = [engine.connect() for _ in range(engine.pool.size())]
    try:
        assert all("sqlite_begin" not in conn.info for conn in connections)
    finally:
        for conn in connections:
            conn.close()


def test_reads_after_a_write_do_not_block_each_other(store, engine):
    import time

    from sqlalchemy import text

    n = engine.pool.size()
    with ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(lambda _: store.create_message(SHOP, **MESSAGE), range(n)))
    holder = engine.connect()
    try:
        with holder.begin():
            holder.execute(text("SELECT 1"))
            started = time.perf_counter()
            store.board(SHOP)
            assert time.perf_counter() - started < 1
    finally:
        holder.close()
