"""Versioned anomaly detection (CR §3, §5, §6, §10).

The detector is the *decision* layer: it reads a bounded window of a device's
telemetry, applies the rules from :mod:`app.services.anomaly_rules` against the
device's resolved thresholds, and hands each firing to
:class:`~app.repositories.anomaly_repository.AnomalyRepository`, which owns the
open/extend/resolve lifecycle.

Two properties the CR insists on are enforced here:

* **Detection is advisory, never blocking (CR §10).** The caller runs this
  after a sample is durably stored and swallows failures; raw ingestion does
  not depend on the detector succeeding.
* **Every detection carries its rule version and evidence (CR §5).** The
  ``rule_version`` and the structured evidence (which reading, which limit)
  are written with the event so the verdict is reproducible after the rules
  change.

Staleness is deliberately anchored to wall-clock time: the rule means "no
recent telemetry", so it reflects the moment detection runs. A backfill of
historical samples is expected to trip it; the detector is built for the live
ingest path, where detection follows the sample immediately.

The evaluator is a pure function over samples, so the rules are unit-testable
without a database; only :meth:`AnomalyDetector.reconcile` touches storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import Device, TelemetrySample
from app.repositories.anomaly_repository import AnomalyRepository
from app.repositories.telemetry_repository import TelemetryRepository
from app.services import anomaly_rules as rules
from app.services.anomaly_rules import Detection, ResolvedThresholds

# How many recent samples a pass inspects. Bounded so a backlog cannot make a
# single detection pass unbounded; older episodes are already recorded.
DETECT_WINDOW = 60


@dataclass
class DetectionResult:
    device_id: str
    opened: list[int]
    extended: list[int]
    resolved: list[int]
    firing_types: list[str]
    firing_keys: list[tuple[str, int | None]]


class AnomalyDetector:
    """Evaluate and persist anomaly events for one device."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._telemetry = TelemetryRepository(session)
        self._anomalies = AnomalyRepository(session)

    # --- evaluation -------------------------------------------------------

    def evaluate(
        self,
        device: Device,
        samples_asc: list[TelemetrySample],
        *,
        now: datetime,
    ) -> list[Detection]:
        """Every rule firing for the window, oldest observation first.

        ``samples_asc`` is ordered oldest→newest so the delta rules have their
        predecessor available and the newest reading decides staleness.
        """
        thresholds = rules.resolve_thresholds(device, self._settings)
        detections: list[Detection] = []

        for index, sample in enumerate(samples_asc):
            previous = samples_asc[index - 1] if index > 0 else None
            detections.extend(self._check_sample(thresholds, sample, previous))
            detections.extend(self._check_gap(device, sample, previous))

        if samples_asc:
            stale = self._check_stale(thresholds, samples_asc[-1], now)
            if stale is not None:
                detections.append(stale)

        # Collapse to the latest firing per key so one pass opens/extends each
        # episode at most once; the newest observation wins.
        latest: dict[tuple[str, int | None], Detection] = {}
        for detection in detections:
            latest[(detection.anomaly_type, detection.affected_cell)] = detection
        return list(latest.values())

    def _check_sample(
        self,
        thresholds: ResolvedThresholds,
        sample: TelemetrySample,
        previous: TelemetrySample | None,
    ) -> list[Detection]:
        detections: list[Detection] = []

        # 1. Per-cell voltage out of range.
        for cell_index, raw in enumerate(sample.cell_voltages or [], start=1):
            value = rules.finite(raw)
            if value is None:
                continue
            if value < thresholds.cell_voltage_min_v:
                ratio = (
                    thresholds.cell_voltage_min_v / value if value > 0 else 2.0
                )
                detections.append(
                    Detection(
                        anomaly_type=rules.TYPE_CELL_VOLTAGE,
                        severity=rules.severity_for(ratio=ratio),
                        observed_value=value,
                        expected_value=thresholds.cell_voltage_min_v,
                        threshold=thresholds.cell_voltage_min_v,
                        affected_cell=cell_index,
                        evidence=rules.sample_evidence(
                            sample,
                            limit="min",
                            cell_index=cell_index,
                            cell_voltage_v=value,
                        ),
                    )
                )
            elif value > thresholds.cell_voltage_max_v:
                ratio = value / thresholds.cell_voltage_max_v
                detections.append(
                    Detection(
                        anomaly_type=rules.TYPE_CELL_VOLTAGE,
                        severity=rules.severity_for(ratio=ratio),
                        observed_value=value,
                        expected_value=thresholds.cell_voltage_max_v,
                        threshold=thresholds.cell_voltage_max_v,
                        affected_cell=cell_index,
                        evidence=rules.sample_evidence(
                            sample,
                            limit="max",
                            cell_index=cell_index,
                            cell_voltage_v=value,
                        ),
                    )
                )

        # 2. Cell-to-cell spread (pack-level; ingestion already computes it).
        spread = rules.finite((sample.quality_flags or {}).get("cell_imbalance_v"))
        if spread is None:
            spread = _spread(sample.cell_voltages)
        if spread is not None and spread > thresholds.cell_spread_max_v:
            ratio = spread / thresholds.cell_spread_max_v
            detections.append(
                Detection(
                    anomaly_type=rules.TYPE_CELL_SPREAD,
                    severity=rules.severity_for(ratio=ratio),
                    observed_value=spread,
                    expected_value=thresholds.cell_spread_max_v,
                    threshold=thresholds.cell_spread_max_v,
                    affected_cell=None,
                    evidence=rules.sample_evidence(
                        sample, cell_spread_v=spread
                    ),
                )
            )

        # 3. Temperature out of range.
        temperature = rules.finite(sample.temperature)
        if temperature is not None:
            if temperature > thresholds.temperature_max_c:
                ratio = temperature / thresholds.temperature_max_c
                detections.append(
                    Detection(
                        anomaly_type=rules.TYPE_TEMPERATURE,
                        severity=rules.severity_for(ratio=ratio),
                        observed_value=temperature,
                        expected_value=thresholds.temperature_max_c,
                        threshold=thresholds.temperature_max_c,
                        affected_cell=None,
                        evidence=rules.sample_evidence(
                            sample, limit="max", temperature_c=temperature
                        ),
                    )
                )
            elif temperature < thresholds.temperature_min_c:
                ratio = (
                    thresholds.temperature_min_c / temperature
                    if temperature > 0
                    else 2.0
                )
                detections.append(
                    Detection(
                        anomaly_type=rules.TYPE_TEMPERATURE,
                        severity=rules.severity_for(ratio=ratio),
                        observed_value=temperature,
                        expected_value=thresholds.temperature_min_c,
                        threshold=thresholds.temperature_min_c,
                        affected_cell=None,
                        evidence=rules.sample_evidence(
                            sample, limit="min", temperature_c=temperature
                        ),
                    )
                )

        if previous is not None:
            detections.extend(self._check_jumps(thresholds, sample, previous))

        return detections

    def _check_jumps(
        self,
        thresholds: ResolvedThresholds,
        sample: TelemetrySample,
        previous: TelemetrySample,
    ) -> list[Detection]:
        detections: list[Detection] = []

        # 4. Pack/cell voltage jump vs the previous sample.
        current_v = rules.finite(sample.pack_voltage)
        prev_v = rules.finite(previous.pack_voltage)
        if current_v is not None and prev_v is not None:
            jump = abs(current_v - prev_v)
            if jump > thresholds.voltage_delta_max_v:
                ratio = jump / thresholds.voltage_delta_max_v
                detections.append(
                    Detection(
                        anomaly_type=rules.TYPE_VOLTAGE_JUMP,
                        severity=rules.severity_for(ratio=ratio),
                        observed_value=jump,
                        expected_value=thresholds.voltage_delta_max_v,
                        threshold=thresholds.voltage_delta_max_v,
                        affected_cell=None,
                        evidence=rules.sample_evidence(
                            sample,
                            previous_measured_at=previous.measured_at.isoformat(),
                            previous_pack_voltage=prev_v,
                            delta_v=jump,
                        ),
                    )
                )

        # 5. Current jump vs the previous sample.
        current_a = rules.finite(sample.current)
        prev_a = rules.finite(previous.current)
        if current_a is not None and prev_a is not None:
            jump = abs(current_a - prev_a)
            if jump > thresholds.current_delta_max_a:
                ratio = jump / thresholds.current_delta_max_a
                detections.append(
                    Detection(
                        anomaly_type=rules.TYPE_CURRENT_JUMP,
                        severity=rules.severity_for(ratio=ratio),
                        observed_value=jump,
                        expected_value=thresholds.current_delta_max_a,
                        threshold=thresholds.current_delta_max_a,
                        affected_cell=None,
                        evidence=rules.sample_evidence(
                            sample,
                            previous_measured_at=previous.measured_at.isoformat(),
                            previous_current=prev_a,
                            delta_a=jump,
                        ),
                    )
                )

        return detections

    def _check_gap(
        self,
        device: Device,
        sample: TelemetrySample,
        previous: TelemetrySample | None,
    ) -> list[Detection]:
        if previous is None:
            return []
        expected = self._expected_interval_s(device)
        # A gap is only anomalous past a multiple of the expected interval;
        # otherwise normal jitter would fire constantly.
        limit = max(expected * 10.0, thresholds_gap_floor())
        delta = (sample.measured_at - previous.measured_at).total_seconds()
        if delta <= limit:
            return []
        ratio = delta / limit
        return [
            Detection(
                anomaly_type=rules.TYPE_TELEMETRY_GAP,
                severity=rules.severity_for(ratio=ratio),
                observed_value=delta,
                expected_value=expected,
                threshold=limit,
                affected_cell=None,
                evidence=rules.sample_evidence(
                    sample,
                    gap_seconds=delta,
                    previous_measured_at=previous.measured_at.isoformat(),
                    expected_interval_s=expected,
                ),
            )
        ]

    def _check_stale(
        self,
        thresholds: ResolvedThresholds,
        latest: TelemetrySample,
        now: datetime,
    ) -> Detection | None:
        age = (now - latest.measured_at).total_seconds()
        if age <= thresholds.stale_after_s:
            return None
        ratio = age / thresholds.stale_after_s
        return Detection(
            anomaly_type=rules.TYPE_STALE,
            severity=rules.severity_for(ratio=ratio),
            observed_value=age,
            expected_value=thresholds.stale_after_s,
            threshold=thresholds.stale_after_s,
            affected_cell=None,
            evidence=rules.sample_evidence(
                latest,
                age_seconds=age,
                stale_after_s=thresholds.stale_after_s,
            ),
        )

    def _expected_interval_s(self, device: Device) -> float:
        frequency = float(device.sampling_frequency_hz or 1.0)
        if frequency <= 0:
            return 1.0
        return 1.0 / frequency

    # --- persistence ------------------------------------------------------

    async def reconcile(
        self,
        device: Device,
        *,
        now: datetime | None = None,
    ) -> DetectionResult:
        """Detect over the recent window and persist open/extend/resolve."""
        now = now or datetime.now(timezone.utc)
        samples_desc = await self._telemetry.list_samples(
            device.device_id, descending=True, limit=DETECT_WINDOW
        )
        samples_asc = list(reversed(samples_desc))

        detections = self.evaluate(device, samples_asc, now=now)
        firing_keys = {(d.anomaly_type, d.affected_cell) for d in detections}

        open_events = await self._anomalies.open_events(device.device_id)
        existing = {(e.anomaly_type, e.affected_cell): e for e in open_events}

        opened: list[int] = []
        extended: list[int] = []
        for detection in detections:
            key = (detection.anomaly_type, detection.affected_cell)
            prior = existing.get(key)
            detected_at = _detected_at(detection, samples_asc, now)
            duration = max((now - detected_at).total_seconds(), 0.0)
            fields = {
                "device_id": device.device_id,
                "anomaly_type": detection.anomaly_type,
                "affected_cell": detection.affected_cell,
                "severity": detection.severity,
                "observed_value": detection.observed_value,
                "expected_value": detection.expected_value,
                "threshold": detection.threshold,
                "detected_at": prior.detected_at if prior else detected_at,
                "last_detected_at": now,
                "duration_seconds": (
                    max(duration, prior.duration_seconds) if prior else duration
                ),
                "status": "open",
                "diagnostic_evidence": detection.evidence,
                "likely_causes": detection.causes(),
                "recommended_actions": detection.actions(),
                "rule_version": rules.RULES_VERSION,
            }
            event = await self._anomalies.open_or_extend(fields)
            if prior is None:
                opened.append(event.id)
            else:
                extended.append(event.id)

        resolved: list[int] = []
        for key, event in existing.items():
            if key not in firing_keys:
                await self._anomalies.close_event(event, now)
                resolved.append(event.id)

        await self._session.flush()
        return DetectionResult(
            device_id=device.device_id,
            opened=opened,
            extended=extended,
            resolved=resolved,
            firing_types=sorted({d.anomaly_type for d in detections}),
            firing_keys=sorted(firing_keys, key=lambda k: (k[0], k[1] or -1)),
        )


def _detected_at(
    detection: Detection, samples_asc: list[TelemetrySample], now: datetime
) -> datetime:
    """When the condition was first observable within the inspected window.

    Uses the reading the detection judged; falls back to ``now`` for the
    staleness rule, which is anchored to the absence of a sample.
    """
    measured = detection.evidence.get("measured_at")
    if isinstance(measured, str):
        try:
            return datetime.fromisoformat(measured)
        except ValueError:
            return now
    return now


def _spread(cells: list | None) -> float | None:
    values = [rules.finite(v) for v in (cells or [])]
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return None
    return max(values) - min(values)


def thresholds_gap_floor() -> float:
    """A floor on the gap limit so a 1 Hz device cannot fire on tiny jitter."""
    return 30.0
