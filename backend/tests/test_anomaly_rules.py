"""Unit tests for the anomaly rule catalog and detector (CR §5, §6).

These exercise the *decision* layer without a database: thresholds resolve
correctly from a device's overrides + chemistry profile, the diagnostic
mapping only labels causes confirmed/unconfirmed where a mapping exists, and
the evaluator fires the right rules for synthetic readings.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.db.models import Device, TelemetrySample
from app.services import anomaly_rules as rules
from app.services.anomaly_detection_service import AnomalyDetector

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_device(**overrides) -> Device:
    values = {
        "device_id": "dev-1",
        "name": "Test pack",
        "cell_count": 4,
        "sampling_frequency_hz": 1.0,
        "chemistry": "li_ion",
        "nominal_capacity_ah": None,
        "status": "active",
    }
    values.update(overrides)
    device = Device(**values)
    # Unset nullable threshold columns resolve to the profile; SQLAlchemy
    # leaves them as None on an unflushed instance, which is what we want.
    return device


def make_sample(
    *,
    measured_at: datetime = NOW,
    cell_voltages: list[float] | None = None,
    temperature: float = 25.0,
    current: float = 1.0,
    pack_voltage: float | None = None,
    quality_flags: dict | None = None,
) -> TelemetrySample:
    cells = cell_voltages if cell_voltages is not None else [3.7, 3.7, 3.7, 3.7]
    return TelemetrySample(
        device_id="dev-1",
        message_id="m",
        measured_at=measured_at,
        received_at=measured_at,
        pack_voltage=pack_voltage if pack_voltage is not None else sum(cells),
        current=current,
        temperature=temperature,
        cell_voltages=cells,
        additional_data=None,
        quality_flags=quality_flags,
    )


def detector() -> AnomalyDetector:
    # The detector only uses the session in reconcile(); evaluate() is pure.
    return AnomalyDetector(session=None, settings=Settings())  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# threshold resolution
# --------------------------------------------------------------------------


def test_chemistry_profile_fills_unset_columns() -> None:
    thresholds = rules.resolve_thresholds(make_device(), Settings())
    profile = rules.CHEMISTRY_PROFILES["li_ion"]
    assert thresholds.cell_voltage_max_v == profile.cell_voltage_max_v
    assert thresholds.chemistry == "li_ion"


def test_device_override_beats_chemistry_profile() -> None:
    device = make_device(cell_voltage_max_v=4.0)
    thresholds = rules.resolve_thresholds(device, Settings())
    assert thresholds.cell_voltage_max_v == 4.0
    # An unset column still falls back to the profile.
    assert thresholds.temperature_max_c == rules.CHEMISTRY_PROFILES["li_ion"].temperature_max_c


def test_unknown_chemistry_falls_back_to_default() -> None:
    thresholds = rules.resolve_thresholds(make_device(chemistry="unobtanium"), Settings())
    assert thresholds.chemistry == "unobtanium"
    assert thresholds.cell_voltage_max_v == rules.CHEMISTRY_PROFILES["li_ion"].cell_voltage_max_v


# --------------------------------------------------------------------------
# diagnosis mapping
# --------------------------------------------------------------------------


def test_every_rule_type_has_a_diagnosis() -> None:
    for anomaly_type in rules.DIAGNOSTICS:
        causes, actions = rules.diagnose(anomaly_type)
        assert causes, f"{anomaly_type} has no likely causes"
        assert actions, f"{anomaly_type} has no recommended actions"


def test_causes_are_labelled_confirmed_or_unconfirmed() -> None:
    for anomaly_type in rules.DIAGNOSTICS:
        causes, _ = rules.diagnose(anomaly_type)
        assert any(c["confirmed"] for c in causes), (
            f"{anomaly_type} has no confirmed cause"
        )
        for cause in causes:
            assert isinstance(cause["confirmed"], bool)


def test_unknown_type_has_no_invented_diagnosis() -> None:
    """CR §5: no mapping → no explanation, not a guess."""
    assert rules.diagnose("not_a_real_rule") == ([], [])


# --------------------------------------------------------------------------
# detector firing
# --------------------------------------------------------------------------


def test_healthy_sample_fires_nothing() -> None:
    device = make_device()
    detections = detector().evaluate(device, [make_sample()], now=NOW)
    assert detections == []


def test_cell_voltage_max_fires_for_the_offending_cell() -> None:
    device = make_device(cell_voltage_max_v=4.0)
    sample = make_sample(cell_voltages=[3.7, 4.1, 3.7, 3.7])
    detections = detector().evaluate(device, [sample], now=NOW)
    firing = [d for d in detections if d.anomaly_type == rules.TYPE_CELL_VOLTAGE]
    assert len(firing) == 1
    assert firing[0].affected_cell == 2
    assert firing[0].evidence["cell_voltage_v"] == 4.1


def test_cell_voltage_min_fires() -> None:
    device = make_device(cell_voltage_min_v=3.0)
    sample = make_sample(cell_voltages=[3.7, 2.9, 3.7, 3.7])
    detections = detector().evaluate(device, [sample], now=NOW)
    firing = [d for d in detections if d.anomaly_type == rules.TYPE_CELL_VOLTAGE]
    assert len(firing) == 1
    assert firing[0].evidence["limit"] == "min"


def test_excessive_spread_fires_from_quality_flag() -> None:
    device = make_device(cell_spread_max_v=0.05)
    sample = make_sample(quality_flags={"cell_imbalance_v": 0.2})
    detections = detector().evaluate(device, [sample], now=NOW)
    assert rules.TYPE_CELL_SPREAD in {d.anomaly_type for d in detections}


def test_temperature_out_of_range_fires() -> None:
    device = make_device(temperature_max_c=40.0)
    sample = make_sample(temperature=55.0)
    detections = detector().evaluate(device, [sample], now=NOW)
    assert rules.TYPE_TEMPERATURE in {d.anomaly_type for d in detections}


def test_voltage_and_current_jump_fire_against_previous() -> None:
    device = make_device(voltage_delta_max_v=0.1, current_delta_max_a=5.0)
    first = make_sample(measured_at=NOW, pack_voltage=14.8, current=1.0)
    second = make_sample(
        measured_at=NOW + timedelta(seconds=1), pack_voltage=15.5, current=20.0
    )
    detections = detector().evaluate(device, [first, second], now=NOW)
    types = {d.anomaly_type for d in detections}
    assert rules.TYPE_VOLTAGE_JUMP in types
    assert rules.TYPE_CURRENT_JUMP in types


def test_stale_fires_when_latest_sample_is_old() -> None:
    device = make_device(stale_after_s=900.0)
    sample = make_sample(measured_at=NOW - timedelta(seconds=2000))
    detections = detector().evaluate(device, [sample], now=NOW)
    assert rules.TYPE_STALE in {d.anomaly_type for d in detections}


def test_telemetry_gap_fires_past_the_floor() -> None:
    device = make_device(sampling_frequency_hz=1.0)
    first = make_sample(measured_at=NOW)
    second = make_sample(measured_at=NOW + timedelta(minutes=5))
    detections = detector().evaluate(device, [first, second], now=NOW + timedelta(minutes=5))
    assert rules.TYPE_TELEMETRY_GAP in {d.anomaly_type for d in detections}


def test_one_detection_per_key_keeps_the_newest() -> None:
    """A sustained condition must yield one firing, not one per sample (CR §6)."""
    device = make_device(cell_voltage_max_v=4.0)
    samples = [
        make_sample(measured_at=NOW + timedelta(seconds=i), cell_voltages=[3.7, 4.1, 3.7, 3.7])
        for i in range(5)
    ]
    detections = detector().evaluate(device, samples, now=NOW)
    cell_voltage = [d for d in detections if d.anomaly_type == rules.TYPE_CELL_VOLTAGE]
    assert len(cell_voltage) == 1
