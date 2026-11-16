"""Add a structured finish time (eta_at) to repair orders."""

from alembic import op
from sqlalchemy import Column, DateTime

revision = "0005_repair_order_eta_at"
down_revision = "0004_call_summaries"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("repair_orders", Column("eta_at", DateTime(timezone=True)))


def downgrade():
    op.drop_column("repair_orders", "eta_at")
