"""Add a nullable vehicle column to messages."""

from alembic import op
from sqlalchemy import Column, Text

revision = "0002_message_vehicle"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", Column("vehicle", Text()))


def downgrade():
    op.drop_column("messages", "vehicle")
