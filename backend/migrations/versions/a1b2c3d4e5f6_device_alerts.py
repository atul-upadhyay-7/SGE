"""device alerts + last_aggregated_at

Revision ID: a1b2c3d4e5f6
Revises: d7a7d00f12e6
Create Date: 2026-10-20 00:00:00.000000+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'd7a7d00f12e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('devices', sa.Column('last_aggregated_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table('device_alerts',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('device_id', sa.String(length=64), nullable=False),
        sa.Column('rule_code', sa.String(length=64), nullable=False),
        sa.Column('severity', sa.String(length=16), nullable=False),
        sa.Column('message', sa.String(length=512), nullable=False),
        sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('acked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('acked_by', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['device_id'], ['devices.device_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_device_alerts_device_id'), 'device_alerts', ['device_id'], unique=False)
    op.create_index(op.f('ix_device_alerts_status'), 'device_alerts', ['status'], unique=False)
    op.create_index('ix_alert_device_rule', 'device_alerts', ['device_id', 'rule_code'], unique=False)
    op.create_index(
        'uq_alert_open_per_device_rule',
        'device_alerts',
        ['device_id', 'rule_code'],
        unique=True,
        postgresql_where=sa.text("status <> 'resolved'"),
    )


def downgrade() -> None:
    op.drop_index('uq_alert_open_per_device_rule', table_name='device_alerts')
    op.drop_index('ix_alert_device_rule', table_name='device_alerts')
    op.drop_index(op.f('ix_device_alerts_status'), table_name='device_alerts')
    op.drop_index(op.f('ix_device_alerts_device_id'), table_name='device_alerts')
    op.drop_table('device_alerts')
    op.drop_column('devices', 'last_aggregated_at')
