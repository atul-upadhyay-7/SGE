"""Reports gain heartbeat_at so queued-build recovery follows liveness.

Revision ID: f217a3b4c901
Revises: e10622a1
"""
import sqlalchemy as sa
from alembic import op

revision = "f217a3b4c901"
down_revision = "e10622a1"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("reports", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("reports", "heartbeat_at")
