"""Initial persistence schema (frozen independently of application metadata)."""

from alembic import op
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
)

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

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
        Column("created_at", DateTime(timezone=True), nullable=False),
    ]


def text_columns(*names):
    return [Column(name, Text, nullable=False) for name in names]


repair_orders = Table(
    "repair_orders",
    metadata,
    *common_columns(),
    Column("ro_number", String(20), nullable=False),
    *text_columns("customer_name", "phone", "plate", "vehicle", "status", "note", "eta", "balance_estimate"),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("shop_id", "ro_number"),
)
appointments = Table(
    "appointments",
    metadata,
    *common_columns(),
    Column("slot_start", DateTime(timezone=True), nullable=False),
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


def upgrade():
    metadata.create_all(op.get_bind())


def downgrade():
    metadata.drop_all(op.get_bind())
