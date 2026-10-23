"""anomaly events, anomaly actions, per-device thresholds (CR §3)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-20 00:00:00.000000+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Device chemistry + nominal capacity: chemistry selects the default
    # anomaly-threshold profile (CR §5), capacity is the SoH reference.
    op.add_column(
        'devices',
        sa.Column('chemistry', sa.String(length=32), nullable=False, server_default='li_ion'),
    )
    op.add_column(
        'devices',
        sa.Column('nominal_capacity_ah', sa.Float(), nullable=True),
    )

    # Per-device anomaly thresholds (CR §5). All nullable: NULL means "use the
    # chemistry default", so a device stores only its deliberate overrides.
    for column in (
        sa.Column('cell_voltage_min_v', sa.Float(), nullable=True),
        sa.Column('cell_voltage_max_v', sa.Float(), nullable=True),
        sa.Column('cell_spread_max_v', sa.Float(), nullable=True),
        sa.Column('temperature_min_c', sa.Float(), nullable=True),
        sa.Column('temperature_max_c', sa.Float(), nullable=True),
        sa.Column('current_delta_max_a', sa.Float(), nullable=True),
        sa.Column('voltage_delta_max_v', sa.Float(), nullable=True),
        sa.Column('stale_after_s', sa.Float(), nullable=True),
    ):
        op.add_column('devices', column)

    op.create_table(
        'anomaly_events',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('device_id', sa.String(length=64), nullable=False),
        sa.Column('anomaly_type', sa.String(length=64), nullable=False),
        sa.Column('severity', sa.String(length=16), nullable=False),
        sa.Column('affected_cell', sa.Integer(), nullable=True),
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_detected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('observed_value', sa.Float(), nullable=True),
        sa.Column('expected_value', sa.Float(), nullable=True),
        sa.Column('threshold', sa.Float(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('diagnostic_evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('likely_causes', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('recommended_actions', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('rule_version', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['device_id'], ['devices.device_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_anomaly_events_device_id'), 'anomaly_events', ['device_id'], unique=False)
    op.create_index(op.f('ix_anomaly_events_anomaly_type'), 'anomaly_events', ['anomaly_type'], unique=False)
    op.create_index(op.f('ix_anomaly_events_severity'), 'anomaly_events', ['severity'], unique=False)
    op.create_index(op.f('ix_anomaly_events_status'), 'anomaly_events', ['status'], unique=False)
    op.create_index('ix_anomaly_device_detected', 'anomaly_events', ['device_id', 'detected_at'], unique=False)
    op.create_index(
        'ix_anomaly_device_type_cell',
        'anomaly_events',
        ['device_id', 'anomaly_type', 'affected_cell'],
        unique=False,
    )
    op.create_index(
        'uq_anomaly_open_per_key',
        'anomaly_events',
        ['device_id', 'anomaly_type', 'affected_cell'],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )

    op.create_table(
        'anomaly_actions',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('anomaly_id', sa.BigInteger(), nullable=False),
        sa.Column('action_type', sa.String(length=32), nullable=False),
        sa.Column('actor', sa.String(length=128), nullable=True),
        sa.Column('note', sa.String(length=2048), nullable=True),
        sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['anomaly_id'], ['anomaly_events.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_anomaly_actions_anomaly_id'), 'anomaly_actions', ['anomaly_id'], unique=False)
    op.create_index(op.f('ix_anomaly_actions_action_type'), 'anomaly_actions', ['action_type'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_anomaly_actions_action_type'), table_name='anomaly_actions')
    op.drop_index(op.f('ix_anomaly_actions_anomaly_id'), table_name='anomaly_actions')
    op.drop_table('anomaly_actions')

    op.drop_index('uq_anomaly_open_per_key', table_name='anomaly_events')
    op.drop_index('ix_anomaly_device_type_cell', table_name='anomaly_events')
    op.drop_index('ix_anomaly_device_detected', table_name='anomaly_events')
    op.drop_index(op.f('ix_anomaly_events_status'), table_name='anomaly_events')
    op.drop_index(op.f('ix_anomaly_events_severity'), table_name='anomaly_events')
    op.drop_index(op.f('ix_anomaly_events_anomaly_type'), table_name='anomaly_events')
    op.drop_index(op.f('ix_anomaly_events_device_id'), table_name='anomaly_events')
    op.drop_table('anomaly_events')

    for name in (
        'stale_after_s',
        'voltage_delta_max_v',
        'current_delta_max_a',
        'temperature_max_c',
        'temperature_min_c',
        'cell_spread_max_v',
        'cell_voltage_max_v',
        'cell_voltage_min_v',
    ):
        op.drop_column('devices', name)

    op.drop_column('devices', 'nominal_capacity_ah')
    op.drop_column('devices', 'chemistry')
