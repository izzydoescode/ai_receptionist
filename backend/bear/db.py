"""Shared schema and database configuration."""

import os
from datetime import UTC

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
)
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Persist UTC and restore timezone information on SQLite too."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone aware")
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def common_columns():
    return [
        Column("id", Integer, primary_key=True),
        Column("shop_id", String(64), nullable=False, index=True),
        Column("public_id", String(7), nullable=False, unique=True),
        Column("created_at", UTCDateTime(), nullable=False),
    ]


def text_columns(*names):
    return [Column(name, Text, nullable=False) for name in names]


repair_orders = Table(
    "repair_orders",
    metadata,
    *common_columns(),
    Column("ro_number", String(20), nullable=False),
    *text_columns("customer_name", "phone", "plate", "vehicle", "status", "note", "eta", "balance_estimate"),
    Column("updated_at", UTCDateTime(), nullable=False),
    UniqueConstraint("shop_id", "ro_number"),
)
appointments = Table(
    "appointments",
    metadata,
    *common_columns(),
    Column("slot_start", UTCDateTime(), nullable=False),
    Column("bay", Integer, nullable=False),
    *text_columns("customer_name", "phone", "vehicle", "service", "language"),
    Column("notes", Text),
    CheckConstraint("bay >= 1", name="bay_positive"),
    UniqueConstraint("shop_id", "slot_start", "bay", name="uq_appointments_shop_slot_bay"),
)
tows = Table(
    "tows",
    metadata,
    *common_columns(),
    *text_columns("customer_name", "phone", "pickup_location", "vehicle", "issue"),
    Column("is_safe", Boolean, nullable=False),
    *text_columns("destination", "language", "status"),
)
messages = Table(
    "messages",
    metadata,
    *common_columns(),
    *text_columns("caller_name", "phone", "reason", "urgency", "callback_language"),
)
audit_events = Table(
    "audit_events",
    metadata,
    *common_columns(),
    Column("event", String(64), nullable=False),
    Column("entity_public_id", String(7)),
    Column("outcome", String(32)),
)
# One row per finished call, from the ElevenLabs post-call webhook. Transcripts and audio are never
# stored here; the summary is ElevenLabs' short analysis of the call.
call_summaries = Table(
    "call_summaries",
    metadata,
    *common_columns(),
    Column("conversation_id", String(64), nullable=False, unique=True),
    Column("started_at", UTCDateTime()),
    Column("duration_secs", Integer),
    Column("outcome", String(16), nullable=False),
    Column("summary", Text, nullable=False),
    Column("title", Text),
    Column("language", String(8)),
)
REQUIRED_TABLES = frozenset(metadata.tables)


def normalize_database_url(url):
    if url.startswith(("postgres://", "postgresql://")):
        return "postgresql+psycopg://" + url.split("://", 1)[1]
    if url.startswith(("postgresql+psycopg://", "sqlite://")):
        return url
    raise ValueError("unsupported database URL")


def database_url():
    return normalize_database_url(os.getenv("DATABASE_URL", "sqlite:///./vicecity.db"))


def make_engine(url):
    url = normalize_database_url(url)
    if url.startswith("sqlite://"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def connect(dbapi_connection, connection_record):
            dbapi_connection.isolation_level = None

        @event.listens_for(engine, "begin")
        def begin(connection):
            connection.exec_driver_sql("BEGIN IMMEDIATE")

        return engine
    return create_engine(url, pool_pre_ping=True)


def create_schema(engine):
    metadata.create_all(engine)


def check_schema(engine):
    if not REQUIRED_TABLES.issubset(inspect(engine).get_table_names()):
        raise RuntimeError("Database schema missing; run alembic upgrade head")
