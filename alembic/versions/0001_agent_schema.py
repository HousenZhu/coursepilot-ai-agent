"""Create the CoursePilot-owned schema and tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_agent_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS agent")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="agent",
    )
    op.create_index("ix_agent_conversations_user_id", "conversations", ["user_id"], schema="agent")

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent.conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="agent",
    )
    op.create_index(
        "ix_agent_messages_conversation_created",
        "messages",
        ["conversation_id", "created_at"],
        schema="agent",
    )

    op.create_table(
        "study_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("course_id", sa.String(64)),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("plan", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="agent",
    )
    op.create_index("ix_agent_study_plans_user_id", "study_plans", ["user_id"], schema="agent")
    op.create_index("ix_agent_study_plans_course_id", "study_plans", ["course_id"], schema="agent")

    op.create_table(
        "document_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("course_id", sa.String(64), nullable=False),
        sa.Column("content_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("page", sa.Integer()),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("chunk_metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="agent",
    )
    op.create_index(
        "ix_agent_chunks_course_content",
        "document_chunks",
        ["course_id", "content_id"],
        schema="agent",
    )
    op.create_index("ix_agent_chunks_hash", "document_chunks", ["content_hash"], schema="agent")

    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("prompt_tokens", sa.Integer()),
        sa.Column("completion_tokens", sa.Integer()),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("error_code", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="agent",
    )
    op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"], schema="agent")
    op.create_index("ix_agent_runs_user_id", "agent_runs", ["user_id"], schema="agent")


def downgrade() -> None:
    op.drop_table("agent_runs", schema="agent")
    op.drop_table("document_chunks", schema="agent")
    op.drop_table("study_plans", schema="agent")
    op.drop_table("messages", schema="agent")
    op.drop_table("conversations", schema="agent")
    op.execute("DROP SCHEMA IF EXISTS agent")
