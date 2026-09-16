"""Keep displayed citations and plans with committed conversation messages."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_message_payload"
down_revision = "0002_reliable_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("response_payload", JSONB), schema="agent")


def downgrade() -> None:
    op.drop_column("messages", "response_payload", schema="agent")
