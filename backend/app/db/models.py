"""ORM models for the backend schema (PRD §5).

Raw telemetry is stored untouched; derived cycle features and predictions
live in their own tables so a model change can never rewrite history.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Device(Base):
    """A registered BMS device (FR-01)."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    cell_count: Mapped[int] = mapped_column(Integer)
    sampling_frequency_hz: Mapped[float] = mapped_column(Float, default=1.0)
    # Chemistry selects the default anomaly-threshold profile (CR §5); the
    # nominal capacity is the reference for a capacity-based SoH, if any.
    chemistry: Mapped[str] = mapped_column(String(32), default="li_ion")
    nominal_capacity_ah: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    credential_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When the monitoring loop last replayed this device's telemetry into
    # cycles/predictions; the loop skips a device that has not gained a sample
    # since then, so an idle device costs no work each tick (Phase 5).
    last_aggregated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Per-device anomaly thresholds (CR §5) ---------------------------
    # Battery-specific limits for the target hardware and chemistry. Every
    # value is nullable: ``None`` falls back to the chemistry default in
    # :mod:`app.services.anomaly_rules`, so a device carries only the
    # overrides that differ from its chemistry profile. No universal safety
    # limit is hardcoded as a device value.
    cell_voltage_min_v: Mapped[float | None] = mapped_column(Float, nullable=True)
    cell_voltage_max_v: Mapped[float | None] = mapped_column(Float, nullable=True)
    cell_spread_max_v: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_min_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_max_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_delta_max_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    voltage_delta_max_v: Mapped[float | None] = mapped_column(Float, nullable=True)
    stale_after_s: Mapped[float | None] = mapped_column(Float, nullable=True)


class TelemetrySample(Base):
    """One accepted raw telemetry message (FR-05).

    ``cell_voltages`` and the optional payload extensions use JSONB; the
    frequently queried scalars get dedicated columns.
    """

    __tablename__ = "telemetry_samples"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.device_id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[str] = mapped_column(String(128))
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    pack_voltage: Mapped[float | None] = mapped_column(Float, nullable=True)
    current: Mapped[float] = mapped_column(Float)
    temperature: Mapped[float] = mapped_column(Float)
    cell_voltages: Mapped[list] = mapped_column(JSONB)
    additional_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    quality_flags: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "device_id", "message_id", name="uq_telemetry_device_message"
        ),
        Index("ix_telemetry_device_measured", "device_id", "measured_at"),
    )


class TelemetryRejection(Base):
    """An auditable record of a payload that failed ingestion (FR-02, FR-04).

    Every rejection carries a stable machine ``reason`` plus the original
    payload, so an operator can understand *why* a sample never reached the
    telemetry table. This table is the quarantine: it is append-only and
    never queried on the hot path.
    """

    __tablename__ = "telemetry_rejections"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="mqtt")
    reason: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BatteryCycle(Base):
    """A discharge cycle and its aggregated features (FR-06)."""

    __tablename__ = "battery_cycles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.device_id", ondelete="CASCADE"), index=True
    )
    cycle_number: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    capacity_ah: Mapped[float | None] = mapped_column(Float, nullable=True)
    reference_capacity_ah: Mapped[float | None] = mapped_column(Float, nullable=True)
    measured_soh: Mapped[float | None] = mapped_column(Float, nullable=True)
    feature_schema_version: Mapped[str] = mapped_column(String(32), default="v1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "device_id", "cycle_number", name="uq_cycle_device_number"
        ),
    )


class SohPrediction(Base):
    """A stored State-of-Health prediction (FR-07, FR-09)."""

    __tablename__ = "soh_predictions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.device_id", ondelete="CASCADE"), index=True
    )
    cycle_id: Mapped[int | None] = mapped_column(
        ForeignKey("battery_cycles.id", ondelete="SET NULL"), nullable=True
    )
    predicted_soh: Mapped[float] = mapped_column(Float)
    measured_soh: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version: Mapped[str] = mapped_column(String(64))
    feature_schema_version: Mapped[str] = mapped_column(String(32))
    predicted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    input_quality_status: Mapped[str] = mapped_column(String(32), default="ok")

    __table_args__ = (
        Index("ix_prediction_device_predicted", "device_id", "predicted_at"),
    )


class ModelRegistry(Base):
    """Versioned model artifacts and their active/inactive state (FR-07)."""

    __tablename__ = "model_registry"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(128))
    model_version: Mapped[str] = mapped_column(String(64))
    artifact_path: Mapped[str] = mapped_column(String(512))
    feature_schema_version: Mapped[str] = mapped_column(String(32))
    training_dataset_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("model_name", "model_version", name="uq_model_name_version"),
    )


class DeviceAlert(Base):
    """A rule breach raised against a device (Phase 5).

    Alerts are keyed on ``(device_id, rule_code)`` while they are open: a
    rule that keeps firing updates ``last_seen_at`` instead of stacking a
    new row per evaluation, so a 60-second monitor loop cannot flood the
    table. Closing a condition resolves the row; the same rule opening again
    later creates a fresh row.
    """

    __tablename__ = "device_alerts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.device_id", ondelete="CASCADE"), index=True
    )
    rule_code: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    message: Mapped[str] = mapped_column(String(512))
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_alert_device_rule", "device_id", "rule_code"),
        # One open (active or acked) alert per device and rule; the partial
        # index is what lets the evaluator upsert without double-opening.
        Index(
            "uq_alert_open_per_device_rule",
            "device_id",
            "rule_code",
            unique=True,
            postgresql_where=text("status <> 'resolved'"),
        ),
    )


class AnomalyEvent(Base):
    """A diagnosed anomaly against a device (CR §3, §5).

    An event is the *episode*, not the reading: a rule that keeps firing for
    the same ``(device_id, anomaly_type, affected_cell)`` extends the open
    event (``last_detected_at`` and ``duration_seconds`` advance) instead of
    stacking a row per 1 Hz sample. Closing it resolves the row; the same
    condition recurring later opens a new event, which is what makes
    recurrence counts meaningful (CR §6).
    """

    __tablename__ = "anomaly_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.device_id", ondelete="CASCADE"), index=True
    )
    anomaly_type: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    # 1-based cell index, or NULL for pack-level/whole-device anomalies.
    affected_cell: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    observed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    # Evidence is a structured record of *why* the rule fired; the cause and
    # action lists come from the diagnostic mapping and are labelled
    # confirmed/unconfirmed so an unproven explanation is never presented as
    # fact (CR §5).
    diagnostic_evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    likely_causes: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    recommended_actions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # The rule/model identity that produced the event, so a detection is
    # reproducible after the rules change (CR §5).
    rule_version: Mapped[str] = mapped_column(String(32), default="rules-v1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_anomaly_device_detected", "device_id", "detected_at"),
        Index("ix_anomaly_device_type_cell", "device_id", "anomaly_type", "affected_cell"),
        # One open event per (device, type, cell): the partial unique index is
        # what lets the detector extend an episode instead of double-opening,
        # and what stops a 1 Hz condition from minting one row per sample.
        Index(
            "uq_anomaly_open_per_key",
            "device_id",
            "anomaly_type",
            "affected_cell",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )


class AnomalyAction(Base):
    """The audit trail for an anomaly: acks, notes, corrective and resolve (CR §3, §4).

    Append-only. The denormalized ``action_type`` plus ``actor`` make the
    history readable without joining, and the row keeps the note that was
    recorded at the time even if the event is later edited.
    """

    __tablename__ = "anomaly_actions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    anomaly_id: Mapped[int] = mapped_column(
        ForeignKey("anomaly_events.id", ondelete="CASCADE"), index=True
    )
    action_type: Mapped[str] = mapped_column(String(32), index=True)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
