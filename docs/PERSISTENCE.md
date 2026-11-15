# Task 5/6 contract (persistence + admin board)

## Layout
- New package `backend/bear/` (`__init__.py`, `db.py`, `store.py`, `seed.py`). `backend/main.py` stays the FastAPI app and imports `from bear.store import Store, SlotFull` and `from bear.db import ...`.
- Alembic: `backend/alembic.ini` (script_location = migrations) and `backend/migrations/` (`env.py`, `script.py.mako`, `versions/0001_initial.py`). env.py reads the URL from `DATABASE_URL` (through `bear.db.database_url()`), uses `bear.db.metadata` as target_metadata, and enables `render_as_batch` for SQLite. `cd backend && alembic upgrade head` must work.
- Requirements (backend/requirements.txt): add `sqlalchemy>=2.0,<3`, `alembic>=1.13`, `psycopg[binary]>=3.2`.
- `data.json`, `DATA_FILE`, `read_db`, `SEED` and the JSON load/save code path are REMOVED. The demo repair orders move to `bear.seed.DEMO_REPAIR_ORDERS` (same 4 records: RO 1001-1004, same names/phones/plates/vehicles/status/note/eta/balance).

## Settings / env
- `DATABASE_URL` (default `sqlite:///./vicecity.db`, i.e. relative to the CWD).
- `bear.db.normalize_database_url(url)`: `postgres://...` and `postgresql://...` become `postgresql+psycopg://...` (query string such as `?sslmode=require` preserved); `postgresql+psycopg://` and `sqlite://` URLs unchanged; empty or other schemes -> ValueError.
- `bear.db.make_engine(url, timeout_seconds=4)`: normalizes; for SQLite: `check_same_thread=False`, busy `timeout` in seconds (`create_app` passes `TOOL_DEADLINE_MS / 1000` so a locked DB cannot block a worker past the tool deadline), and transactions begin `DEFERRED` unless the connection sets `info["sqlite_begin"] = "IMMEDIATE"` (`Store._write` does this so concurrent writers serialize without also serializing reads); for Postgres: `pool_pre_ping=True` (Neon suspends idle computes).
- `bear.db.create_schema(engine)` = metadata.create_all (used by tests/dev); production uses Alembic.
- `SHOP_ID` env (default `vice-city-auto`; must match `^[a-z0-9][a-z0-9-]{0,63}$` else RuntimeError at create_app). Tools write/read with this shop_id until Task 7 brings per-shop config.
- `create_app()` builds the engine from DATABASE_URL and `app.state.store = Store(engine, write_guard=<deadline check>)`. If any of the 5 tables is missing it raises `RuntimeError` whose message contains `alembic upgrade head`. It does NOT create tables or seed data.
- Secrets: DATABASE_URL may contain a password; it must never be logged or included in error messages (RuntimeError text must not contain the URL).

## Tables (SQLAlchemy 2.x declarative, `bear.db.metadata`)
Every table: integer PK `id`, `shop_id` (String(64), indexed, not null), `public_id` (String(7), unique, not null; one uppercase letter prefix + 6 uppercase hex: A=appointments, R=repair_orders, T=tows, M=messages, E=audit_events), `created_at` (timezone-aware UTC; a TypeDecorator stores UTC and always returns aware UTC datetimes, also on SQLite).
- `repair_orders`: ro_number (String(20)), customer_name, phone (E.164), plate, vehicle, status, note, eta, balance_estimate, updated_at (UTC). UNIQUE(shop_id, ro_number).
- `appointments`: slot_start (UTC), bay (int >=1), customer_name, phone, vehicle, service, language, notes (nullable). UNIQUE(shop_id, slot_start, bay) named `uq_appointments_shop_slot_bay`.
- `tows`: customer_name, phone, pickup_location, vehicle, issue, is_safe (bool), destination, language, status.
- `messages`: caller_name, phone, reason, urgency, callback_language, vehicle (nullable).
- `audit_events`: event (String(64)), entity_public_id (nullable), outcome (nullable String(32)). No PII columns.

## Store API (`bear.store.Store`), all methods return plain dicts (column -> value) or lists of them
- `Store(engine, write_guard=None)`; `write_guard()` is called inside every write transaction right before commit; if it raises, the transaction is rolled back and the exception propagates.
- `book_appointment(shop_id, slot_start, bays, **fields) -> dict` (slot_start aware datetime, any tz; stored UTC). Within one transaction: find the lowest free bay in 1..bays and insert; on IntegrityError (concurrent insert) retry the next bay (savepoint or fresh transaction); when all bays are taken raise `SlotFull`. Also writes audit_event `appointment.booked`.
- `booked_counts(shop_id, start, end) -> dict[datetime(UTC) -> int]` for [start, end).
- `create_tow(shop_id, **fields)`, `create_message(shop_id, **fields)` -> dict (audit `tow.requested` / `message.taken`).
- `list_repair_orders(shop_id) -> list[dict]`.
- `upsert_repair_order(shop_id, **fields) -> dict` keyed by (shop_id, ro_number).
- `record_audit(shop_id, event, entity_public_id=None, outcome=None)` (repair-status lookups record `repair_status.lookup` with outcome `verified` / `failed` / `needs_info`).
- `board(shop_id, limit=50) -> {"appointments", "tows", "messages", "repair_orders"}` each newest first by created_at (repair_orders by updated_at), at most `limit` rows, only that shop.
- `reset_shop(shop_id)` deletes that shop's appointments/tows/messages/audit_events/repair_orders (other shops untouched). Dev `/admin/reset` = reset_shop + seed demo for settings.shop_id.

## Seed
- `python -m bear.seed --demo [--shop SHOP_ID]` (run from backend/) upserts the 4 demo repair orders for the shop (default `vice-city-auto`), idempotent, prints a one-line count without PII. `bear.seed.main(argv) -> int`; no `--demo` -> usage error (SystemExit 2 from argparse). Uses DATABASE_URL; requires the schema (clear error mentioning `alembic upgrade head` otherwise).

## Endpoint behaviour (unchanged responses unless stated)
- book_appointment: slot rules as today (BAYS_PER_SLOT=2 per hour slot); full slot -> same `booked: False` "That slot is full." response with alternatives computed from `booked_counts`. Confirmation = appointment public_id.
- tickets for tows/messages = public_id.
- check_repair_status: same verification logic over `list_repair_orders(settings.shop_id)`, plus audit.
- Deadline: the write_guard raises the private deadline exception when the request deadline has passed, so a timed-out write never commits.

## Task 6 admin board
- `GET /admin?shop=<shop_id>` (default settings.shop_id; invalid pattern -> 400 `{"detail": "invalid shop"}`), Basic auth unchanged, everything html-escaped. Shows `store.board(shop, 50)`.
- Columns: Appointments: id(public_id), when (slot_start), customer_name, phone, vehicle, service, language. Tows: id, created, customer_name, phone, pickup_location, issue, is_safe. Messages: id, created, caller_name, phone, vehicle, reason, urgency, callback_language. Repair orders: ro, customer, vehicle, status, eta.
- Timestamps rendered in the shop TZ (settings.shop_tz) as `YYYY-MM-DD HH:MM TZ` e.g. `2026-11-16 10:00 EST`.
- Title/heading still use settings.shop_name; the board shows only the requested shop's rows.
