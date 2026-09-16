"""Durable request identity and atomic document publication metadata."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002_reliable_runs"
down_revision = "0001_agent_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("request_hash", sa.String(64)),
        sa.Column("final_response", JSONB),
    ):
        op.add_column("agent_runs", column, schema="agent")
    op.create_index("uq_run_request", "agent_runs", ["user_id", "idempotency_key"], unique=True, schema="agent")
    op.create_table(
        "document_versions",
        sa.Column("content_id", sa.String(64), primary_key=True),
        sa.Column("course_id", sa.String(64), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("pipeline_version", sa.String(300), nullable=False),
        sa.Column("active_version", sa.String(64), nullable=False),
        sa.Column("page_hashes", JSONB, nullable=False),
        schema="agent",
    )
    op.execute("CREATE INDEX ix_chunks_fts ON agent.document_chunks USING gin (to_tsvector('english', chunk_text))")


def downgrade() -> None:
    op.execute("DROP INDEX agent.ix_chunks_fts")
    op.drop_table("document_versions", schema="agent")
    op.drop_index("uq_run_request", table_name="agent_runs", schema="agent")
    for name in ("final_response", "request_hash", "idempotency_key"):
        op.drop_column("agent_runs", name, schema="agent")
