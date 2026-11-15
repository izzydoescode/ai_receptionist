"""Call summaries from the ElevenLabs post-call webhook (frozen independently of application metadata)."""

from alembic import op
from sqlalchemy import Column, DateTime, Integer, String, Text

revision = "0002_call_summaries"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "call_summaries",
        Column("id", Integer, primary_key=True),
        Column("shop_id", String(64), nullable=False),
        Column("public_id", String(7), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("conversation_id", String(64), nullable=False),
        Column("started_at", DateTime(timezone=True)),
        Column("duration_secs", Integer),
        Column("outcome", String(16), nullable=False),
        Column("summary", Text, nullable=False),
        Column("title", Text),
        Column("language", String(8)),
    )
    op.create_index("ix_call_summaries_shop_id", "call_summaries", ["shop_id"])
    with op.batch_alter_table("call_summaries") as batch:
        batch.create_unique_constraint("uq_call_summaries_public_id", ["public_id"])
        batch.create_unique_constraint("uq_call_summaries_conversation_id", ["conversation_id"])


def downgrade():
    op.drop_table("call_summaries")
