"""Transaction-scoped storage; no sessions or connections are shared."""

import secrets
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import IntegrityError

from bear.db import appointments, audit_events, call_summaries, messages, metadata, repair_orders, tows


class SlotFull(Exception):
    """Every bay in the requested slot is occupied."""


class Store:
    def __init__(self, engine, write_guard=None):
        self.engine = engine
        self.write_guard = write_guard

    @contextmanager
    def _write(self):
        with self.engine.connect() as conn:
            conn.info["sqlite_begin"] = "IMMEDIATE"
            with conn.begin():
                yield conn
                if self.write_guard is not None:
                    self.write_guard()

    def _insert(self, conn, table, shop_id, prefix, created_at=None, **fields):
        values = dict(
            shop_id=shop_id,
            created_at=(created_at or datetime.now(UTC)).astimezone(UTC),
            **fields,
        )
        for _ in range(5):
            values["public_id"] = prefix + secrets.token_hex(3).upper()
            try:
                with conn.begin_nested():
                    return dict(conn.execute(insert(table).values(**values).returning(table)).mappings().one())
            except IntegrityError:
                # Query after rollback so this also works on PostgreSQL, where
                # the failed statement leaves its savepoint aborted.
                if conn.scalar(select(table.c.id).where(table.c.public_id == values["public_id"])) is None:
                    raise
        raise RuntimeError("could not allocate public id")

    def _audit(self, conn, shop_id, event, entity_public_id=None, outcome=None, created_at=None):
        return self._insert(
            conn,
            audit_events,
            shop_id,
            "E",
            created_at,
            event=event,
            entity_public_id=entity_public_id,
            outcome=outcome,
        )

    def book_appointment(self, shop_id, slot_start, bays, *, created_at=None, **fields):
        slot_start = slot_start.astimezone(UTC)
        with self._write() as conn:
            occupied = set(
                conn.scalars(
                    select(appointments.c.bay).where(
                        appointments.c.shop_id == shop_id,
                        appointments.c.slot_start == slot_start,
                    )
                )
            )
            for bay in range(1, bays + 1):
                if bay in occupied:
                    continue
                try:
                    row = self._insert(
                        conn,
                        appointments,
                        shop_id,
                        "A",
                        created_at,
                        slot_start=slot_start,
                        bay=bay,
                        **fields,
                    )
                except IntegrityError:
                    if (
                        conn.scalar(
                            select(appointments.c.id).where(
                                appointments.c.shop_id == shop_id,
                                appointments.c.slot_start == slot_start,
                                appointments.c.bay == bay,
                            )
                        )
                        is None
                    ):
                        raise
                    continue
                self._audit(conn, shop_id, "appointment.booked", row["public_id"], created_at=created_at)
                return row
            raise SlotFull

    def booked_counts(self, shop_id, start, end):
        with self.engine.connect() as conn:
            return dict(
                conn.execute(
                    select(appointments.c.slot_start, func.count())
                    .where(
                        appointments.c.shop_id == shop_id,
                        appointments.c.slot_start >= start,
                        appointments.c.slot_start < end,
                    )
                    .group_by(appointments.c.slot_start)
                ).all()
            )

    def create_tow(self, shop_id, *, created_at=None, **fields):
        with self._write() as conn:
            row = self._insert(conn, tows, shop_id, "T", created_at, **fields)
            self._audit(conn, shop_id, "tow.requested", row["public_id"], created_at=created_at)
            return row

    def create_message(self, shop_id, *, created_at=None, **fields):
        with self._write() as conn:
            row = self._insert(conn, messages, shop_id, "M", created_at, **fields)
            self._audit(conn, shop_id, "message.taken", row["public_id"], created_at=created_at)
            return row

    def list_repair_orders(self, shop_id):
        return self._list(repair_orders, shop_id)

    def upsert_repair_order(self, shop_id, *, created_at=None, **fields):
        now = (created_at or datetime.now(UTC)).astimezone(UTC)
        # Native upsert preserves identity and is atomic under concurrent writers.
        if self.engine.dialect.name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as upsert
        else:
            from sqlalchemy.dialects.postgresql import insert as upsert
        with self._write() as conn:
            for _ in range(5):
                public_id = "R" + secrets.token_hex(3).upper()
                statement = (
                    upsert(repair_orders)
                    .values(
                        shop_id=shop_id,
                        public_id=public_id,
                        created_at=now,
                        updated_at=now,
                        **fields,
                    )
                    .on_conflict_do_update(
                        index_elements=["shop_id", "ro_number"],
                        set_={**fields, "updated_at": now},
                    )
                    .returning(repair_orders)
                )
                try:
                    with conn.begin_nested():
                        return dict(conn.execute(statement).mappings().one())
                except IntegrityError:
                    if conn.scalar(select(repair_orders.c.id).where(repair_orders.c.public_id == public_id)) is None:
                        raise
            raise RuntimeError("could not allocate public id")

    def save_call_summary(self, shop_id, *, created_at=None, **fields):
        """Store one finished call. Returns None when the conversation was already saved (a webhook retry)."""
        if self.engine.dialect.name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as upsert
        else:
            from sqlalchemy.dialects.postgresql import insert as upsert
        now = (created_at or datetime.now(UTC)).astimezone(UTC)
        with self._write() as conn:
            for _ in range(5):
                public_id = "C" + secrets.token_hex(3).upper()
                statement = (
                    upsert(call_summaries)
                    .values(shop_id=shop_id, public_id=public_id, created_at=now, **fields)
                    .on_conflict_do_nothing(index_elements=["conversation_id"])
                    .returning(call_summaries)
                )
                try:
                    with conn.begin_nested():
                        row = conn.execute(statement).mappings().one_or_none()
                except IntegrityError:
                    # A public_id collision should retry; any other constraint must surface.
                    if conn.scalar(select(call_summaries.c.id).where(call_summaries.c.public_id == public_id)) is None:
                        raise
                    continue
                if row is None:
                    return None
                row = dict(row)
                self._audit(conn, shop_id, "call.summarized", row["public_id"], created_at=created_at)
                return row
            raise RuntimeError("could not allocate a public id")

    def record_audit(self, shop_id, event, entity_public_id=None, outcome=None, *, created_at=None):
        with self._write() as conn:
            return self._audit(conn, shop_id, event, entity_public_id, outcome, created_at)

    def _list(self, table, shop_id, limit=None):
        order = table.c.updated_at if table is repair_orders else table.c.created_at
        statement = select(table).where(table.c.shop_id == shop_id).order_by(order.desc(), table.c.id.desc())
        if limit is not None:
            statement = statement.limit(limit)
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(statement).mappings()]

    def board(self, shop_id, limit=50):
        return {
            table.name: self._list(table, shop_id, limit)
            for table in (appointments, tows, messages, repair_orders, call_summaries)
        }

    def reset_shop(self, shop_id):
        with self._write() as conn:
            for table in metadata.sorted_tables:
                conn.execute(delete(table).where(table.c.shop_id == shop_id))
