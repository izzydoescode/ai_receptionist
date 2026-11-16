"""Add a nullable conversation_id column to messages."""

from alembic import op
from sqlalchemy import Column, Text

revision = "0003_message_conversation_id"
down_revision = "0002_message_vehicle"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", Column("conversation_id", Text()))


def downgrade():
    op.drop_column("messages", "conversation_id")
