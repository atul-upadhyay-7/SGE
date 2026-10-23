"""Settings and registration-schema validation added by the Oct 2026 audit.

Zero/negative values for queue sizes and intervals used to be accepted
silently — and for ``asyncio.Queue(maxsize=...)`` 0 means *unbounded*,
the opposite of the documented back-pressure. Registration likewise
accepted device ids that can never appear in an MQTT topic and chemistry
names that silently fell back to li_ion thresholds.
"""

from __future__ import annotations

import pytest
from app.core.config import Settings
from app.schemas.device import DeviceCreate
from app.schemas.telemetry import TelemetryPayload
from pydantic import ValidationError


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ingest_queue_max": 0},
        {"ingest_batch_size": 0},
        {"ingest_flush_interval_s": 0},
        {"monitor_interval_s": 0},
        {"stream_queue_max": 0},
        {"report_queue_max_pending": 0},
        {"db_pool_size": 0},
        {"telemetry_max_future_skew_s": 0},
    ],
)
def test_non_positive_operational_settings_are_rejected(kwargs) -> None:
    with pytest.raises(ValidationError):
        Settings(**kwargs)


def test_production_rejects_wildcard_cors_with_credentials() -> None:
    settings = Settings(
        environment="production",
        admin_api_key="a" * 12,
        operator_api_key="o" * 12,
        viewer_api_key="v" * 12,
        cors_allow_origins="*",
        cors_allow_credentials=True,
    )
    problems = settings.access_control_problems()
    assert any("CORS" in problem for problem in problems)


def test_device_id_must_be_topic_safe() -> None:
    with pytest.raises(ValidationError):
        DeviceCreate(device_id="pack/1", name="x", cell_count=3)
    with pytest.raises(ValidationError):
        DeviceCreate(device_id="pack 1", name="x", cell_count=3)
    assert DeviceCreate(device_id="PACK-1_a", name="x", cell_count=3)


def test_chemistry_must_name_a_known_profile() -> None:
    with pytest.raises(ValidationError):
        DeviceCreate(
            device_id="PACK1", name="x", cell_count=3, chemistry="li-lon"
        )
    assert DeviceCreate(
        device_id="PACK1", name="x", cell_count=3, chemistry="lifepo4"
    )


def test_cell_voltages_are_bounded() -> None:
    with pytest.raises(ValidationError):
        TelemetryPayload(
            schema_version=1,
            message_id="m1",
            device_id="PACK1",
            measured_at="2026-10-16T05:30:00Z",
            current=1.0,
            temperature=25.0,
            cell_voltages=[4.0] * 1025,
        )
