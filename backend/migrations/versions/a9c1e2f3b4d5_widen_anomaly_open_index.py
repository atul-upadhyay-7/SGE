"""Widen the anomaly open-episode partial unique index.

The app treats open, acknowledged and investigating as "open"
(AnomalyRepository.open_event looks up all three), but
uq_anomaly_open_per_key only covered status='open', so two
concurrent detection passes could double-open an episode once
it had been acknowledged or was under investigation. Recreate
the index over all three non-terminal states.

Revision ID: a9c1e2f3b4d5
Revises: f217a3b4c901
"""

import sqlalchemy as sa
from alembic import op

revision = "a9c1e2f3b4d5"
down_revision = "f217a3b4c901"
branch_labels = None
depends_on = None

_INDEX = "uq_anomaly_open_per_key"
_COLUMNS = ["device_id", "anomaly_type", "affected_cell"]


def upgrade() -> None:
    op.drop_index(_INDEX, table_name="anomaly_events")
    op.create_index(
        _INDEX,
        "anomaly_events",
        _COLUMNS,
        unique=True,
        postgresql_where=sa.text(
            "status IN ('open', 'acknowledged', 'investigating')"
        ),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="anomaly_events")
    op.create_index(
        _INDEX,
        "anomaly_events",
        _COLUMNS,
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
