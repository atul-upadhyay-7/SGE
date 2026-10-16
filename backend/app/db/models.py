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
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    credential_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


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
