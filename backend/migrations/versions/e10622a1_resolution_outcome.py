"""Persist anomaly resolution outcome; false_positive uses existing status column.

Revision ID: e10622a1
Revises: 6bf91683f182
"""
import sqlalchemy as sa
from alembic import op

revision = "e10622a1"
down_revision = "6bf91683f182"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("anomaly_events", sa.Column("resolution_outcome", sa.String(2048), nullable=True))


def downgrade():
    op.drop_column("anomaly_events", "resolution_outcome")
