"""conversations and messages (playground)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-13

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("model_id", sa.BigInteger(), sa.ForeignKey("models.id", ondelete="SET NULL")),
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column("top_p", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("max_tokens", sa.Integer()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_conversations_user_id_updated_at", "conversations", ["user_id", "updated_at"]
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "conversation_id",
            sa.BigInteger(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("role IN ('system','user','assistant')", name="ck_messages_role"),
    )
    op.create_index("ix_messages_conversation_id_id", "messages", ["conversation_id", "id"])


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("conversations")
