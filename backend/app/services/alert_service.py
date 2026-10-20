"""Rule evaluation and reconciliation for device alerts (Phase 5).

The evaluator is a pure decision function: it reads the *persisted* state
(the newest telemetry sample, the newest SoH prediction, and the device's
own ``last_seen_at``) and returns the set of rules that should be firing
right now. Opening, updating and resolving the stored rows is the
repository's job, so the rules can be unit-tested without a database.

The rules mirror the offline Grafana/Prometheus alerting path
(``scripts/make_alert_rules.py``): the same two thresholds, plus device
staleness and the cell-imbalance quality flag the ingestion path already
computes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import Device
from app.repositories.alert_repository import AlertRepository
from app.repositories.cycle_repository import CycleRepository
from app.repositories.telemetry_repository import TelemetryRepository

RULE_SOH_BELOW_RETIREMENT = "soh_below_retirement"
RULE_TEMPERATURE_OUT_OF_RANGE = "temperature_out_of_range"
RULE_DEVICE_STALE = "device_stale"
RULE_CELL_IMBALANCE = "cell_imbalance"

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"

IMBALANCE_FLAG = "cell_imbalance_high"


@dataclass
class FiringRule:
    """A rule that should be open right now."""

    rule_code: str
    severity: str
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class EvaluationResult:
    device_id: str
    opened: int = 0
    updated: int = 0
    resolved: int = 0
    firing: list[str] = field(default_factory=list)


class AlertRuleEngine:
    """Evaluate the alert rules for one device against persisted state."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._telemetry = TelemetryRepository(session)
        self._cycles = CycleRepository(session)
        self._alerts = AlertRepository(session)

    async def evaluate(self, device: Device, now: datetime) -> list[FiringRule]:
        """Return every rule that should be open for ``device`` at ``now``."""
        latest = await self._telemetry.latest_sample(device.device_id)
        prediction = await self._cycles.latest_prediction(device.device_id)
        return self._decide(device, latest, prediction, now)

    def _decide(self, device, latest, prediction, now) -> list[FiringRule]:
        rules: list[FiringRule] = []

        if latest is None:
            # A registered device that has never reported is not stale; it is
            # just new. No rules apply until the first sample lands.
            return rules

        # 1. SoH retirement (mirrors scripts/make_alert_rules.py).
        if prediction is not None and prediction.predicted_soh is not None:
            soh = float(prediction.predicted_soh)
            if soh < self._settings.alert_soh_retire_pct:
                rules.append(
                    FiringRule(
                        rule_code=RULE_SOH_BELOW_RETIREMENT,
                        severity=SEVERITY_CRITICAL,
                        message=(
                            f"SoH has dropped to {soh:.1f}%, below the "
                            f"{self._settings.alert_soh_retire_pct:.0f}% "
                            "retirement threshold"
                        ),
                        details={
                            "predicted_soh": soh,
                            "threshold": self._settings.alert_soh_retire_pct,
                            "model_version": prediction.model_version,
                        },
                    )
                )

        # 2. Inputs outside the model's trained operational range.
        temperature = float(latest.temperature)
        if temperature > self._settings.alert_temperature_max_c:
            rules.append(
                FiringRule(
                    rule_code=RULE_TEMPERATURE_OUT_OF_RANGE,
                    severity=SEVERITY_WARNING,
                    message=(
                        f"Peak temperature {temperature:.1f} C exceeds the "
                        f"{self._settings.alert_temperature_max_c:.1f} C "
                        "maximum the SoH model trained on"
                    ),
                    details={
                        "temperature_c": temperature,
                        "trained_max_c": self._settings.alert_temperature_max_c,
                        "measured_at": latest.measured_at.isoformat(),
                    },
                )
            )

        # 3. Device staleness: no sample inside the configured window.
        observed_at = latest.measured_at
        age_s = (now - observed_at).total_seconds()
        if age_s > self._settings.device_stale_after_s:
            rules.append(
                FiringRule(
                    rule_code=RULE_DEVICE_STALE,
                    severity=SEVERITY_WARNING,
                    message=(
                        f"No telemetry for {age_s / 60:.0f} minutes "
                        f"(limit {self._settings.device_stale_after_s / 60:.0f})"
                    ),
                    details={
                        "age_seconds": round(age_s, 3),
                        "last_measured_at": observed_at.isoformat(),
                        "stale_after_s": self._settings.device_stale_after_s,
                    },
                )
            )

        # 4. Cell imbalance: the ingestion path already flagged it.
        flags = latest.quality_flags or {}
        if flags.get(IMBALANCE_FLAG):
            spread = _finite(flags.get("cell_imbalance_v"))
            rules.append(
                FiringRule(
                    rule_code=RULE_CELL_IMBALANCE,
                    severity=SEVERITY_WARNING,
                    message=(
                        "Cell-to-cell voltage spread "
                        f"{spread:.3f} V exceeds the "
                        f"{self._settings.cell_imbalance_warn_v:.3f} V "
                        "warn threshold"
                        if spread is not None
                        else "Cell-to-cell voltage spread exceeds the warn threshold"
                    ),
                    details={
                        "cell_imbalance_v": spread,
                        "threshold": self._settings.cell_imbalance_warn_v,
                        "measured_at": latest.measured_at.isoformat(),
                    },
                )
            )

        return rules


async def reconcile(
    session: AsyncSession,
    device: Device,
    *,
    now: datetime | None = None,
    settings: Settings,
) -> EvaluationResult:
    """Evaluate the rules for one device and persist the open/resolve state."""
    now = now or datetime.now(timezone.utc)
    engine = AlertRuleEngine(session, settings)
    repository = AlertRepository(session)
    firing = await engine.evaluate(device, now)
    firing_codes = {rule.rule_code for rule in firing}

    existing = {
        alert.rule_code: alert for alert in await repository.open_alerts(device.device_id)
    }

    opened = 0
    updated = 0
    for rule in firing:
        if rule.rule_code in existing:
            updated += 1
        else:
            opened += 1
        await repository.open_alert(
            device_id=device.device_id,
            rule_code=rule.rule_code,
            severity=rule.severity,
            message=rule.message,
            details=rule.details,
            seen_at=now,
        )

    resolved = await repository.resolve_alerts_not_in(
        device.device_id, firing_codes, now
    )
    await session.flush()

    return EvaluationResult(
        device_id=device.device_id,
        opened=opened,
        updated=updated,
        resolved=resolved,
        firing=sorted(firing_codes),
    )


def _finite(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
