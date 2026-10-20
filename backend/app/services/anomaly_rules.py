"""Battery-anomaly rule catalog, threshold resolution and diagnosis (CR §5).

This module holds *only* the domain knowledge: which rules exist, what the
thresholds are, how a device's per-device overrides resolve, and the mapping
from a rule to likely causes and corrective actions.

Design contract (CR §5):

* **No universal safety thresholds.** Every limit is either a per-device
  override (``devices.*`` columns) or a chemistry profile below. The profiles
  are *starting points* for a chemistry, not claims of safety; the CR is
  explicit that limits must suit the actual hardware, so a deployment is
  expected to set per-device overrides.
* **Evidence carries the rule/model version.** Each firing includes the
  ``rule_version`` that produced it, so a verdict is reproducible after the
  rules change.
* **Causes and actions are only emitted where a diagnostic mapping exists.**
  Each entry is labelled ``confirmed`` (deterministic from the observation)
  or ``unconfirmed`` (a hypothesis to investigate); the service never presents
  an unproven explanation as fact.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.db.models import Device, TelemetrySample

# Bumped whenever a rule's condition, threshold meaning or diagnosis changes;
# stored on every event so a detection is reproducible (CR §5).
RULES_VERSION = "rules-v1"

# --- anomaly types (rule codes) -------------------------------------------
TYPE_CELL_VOLTAGE = "cell_voltage_out_of_range"
TYPE_CELL_SPREAD = "cell_voltage_spread"
TYPE_TEMPERATURE = "temperature_out_of_range"
TYPE_VOLTAGE_JUMP = "cell_voltage_jump"
TYPE_CURRENT_JUMP = "current_jump"
TYPE_TELEMETRY_GAP = "telemetry_gap"
TYPE_STALE = "stale_measurement"

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"


@dataclass(frozen=True)
class ChemistryProfile:
    """Default limits for a chemistry. Overridable per device."""

    cell_voltage_min_v: float
    cell_voltage_max_v: float
    cell_spread_max_v: float
    temperature_min_c: float
    temperature_max_c: float
    current_delta_max_a: float
    voltage_delta_max_v: float
    stale_after_s: float


# Starting points per chemistry, NOT safety assertions. A device's own
# columns take precedence; these just keep a freshly registered device usable.
CHEMISTRY_PROFILES: dict[str, ChemistryProfile] = {
    "li_ion": ChemistryProfile(
        cell_voltage_min_v=2.5,
        cell_voltage_max_v=4.25,
        cell_spread_max_v=0.10,
        temperature_min_c=0.0,
        temperature_max_c=60.0,
        current_delta_max_a=50.0,
        voltage_delta_max_v=0.30,
        stale_after_s=900.0,
    ),
    "lifepo4": ChemistryProfile(
        cell_voltage_min_v=2.5,
        cell_voltage_max_v=3.65,
        cell_spread_max_v=0.12,
        temperature_min_c=-10.0,
        temperature_max_c=60.0,
        current_delta_max_a=50.0,
        voltage_delta_max_v=0.30,
        stale_after_s=900.0,
    ),
    "nimh": ChemistryProfile(
        cell_voltage_min_v=0.9,
        cell_voltage_max_v=1.55,
        cell_spread_max_v=0.08,
        temperature_min_c=-20.0,
        temperature_max_c=55.0,
        current_delta_max_a=40.0,
        voltage_delta_max_v=0.20,
        stale_after_s=900.0,
    ),
    "lead_acid": ChemistryProfile(
        cell_voltage_min_v=1.75,
        cell_voltage_max_v=2.45,
        cell_spread_max_v=0.15,
        temperature_min_c=-20.0,
        temperature_max_c=50.0,
        current_delta_max_a=50.0,
        voltage_delta_max_v=0.30,
        stale_after_s=1800.0,
    ),
}

_DEFAULT_CHEMISTRY = "li_ion"


@dataclass(frozen=True)
class ResolvedThresholds:
    """A device's effective limits after applying overrides to its profile."""

    cell_voltage_min_v: float
    cell_voltage_max_v: float
    cell_spread_max_v: float
    temperature_min_c: float
    temperature_max_c: float
    current_delta_max_a: float
    voltage_delta_max_v: float
    stale_after_s: float
    chemistry: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "cell_voltage_min_v": self.cell_voltage_min_v,
            "cell_voltage_max_v": self.cell_voltage_max_v,
            "cell_spread_max_v": self.cell_spread_max_v,
            "temperature_min_c": self.temperature_min_c,
            "temperature_max_c": self.temperature_max_c,
            "current_delta_max_a": self.current_delta_max_a,
            "voltage_delta_max_v": self.voltage_delta_max_v,
            "stale_after_s": self.stale_after_s,
            "chemistry": self.chemistry,
        }


def resolve_thresholds(device: Device, settings: Settings) -> ResolvedThresholds:
    """Layer a device's overrides over its chemistry profile.

    A ``None`` device column means "use the profile default"; this is the only
    place the two are combined, so the resolved set is the single source of
    truth every rule reads.
    """
    chemistry = (getattr(device, "chemistry", None) or _DEFAULT_CHEMISTRY).lower()
    profile = CHEMISTRY_PROFILES.get(chemistry, CHEMISTRY_PROFILES[_DEFAULT_CHEMISTRY])

    def pick(column: str, fallback: float) -> float:
        value = getattr(device, column, None)
        return float(value) if value is not None else float(fallback)

    return ResolvedThresholds(
        cell_voltage_min_v=pick("cell_voltage_min_v", profile.cell_voltage_min_v),
        cell_voltage_max_v=pick("cell_voltage_max_v", profile.cell_voltage_max_v),
        cell_spread_max_v=pick("cell_spread_max_v", profile.cell_spread_max_v),
        temperature_min_c=pick("temperature_min_c", profile.temperature_min_c),
        temperature_max_c=pick("temperature_max_c", profile.temperature_max_c),
        current_delta_max_a=pick("current_delta_max_a", profile.current_delta_max_a),
        voltage_delta_max_v=pick("voltage_delta_max_v", profile.voltage_delta_max_v),
        stale_after_s=pick("stale_after_s", profile.stale_after_s),
        # Fall back to the deployment-wide imbalance flag when the profile is
        # looser than what ingestion already flagged.
        chemistry=chemistry,
    )


# --- diagnosis mapping ----------------------------------------------------

Cause = dict[str, Any]
Action = dict[str, Any]


def _cause(text: str, confirmed: bool) -> Cause:
    return {"cause": text, "confirmed": confirmed}


def _action(text: str, *, priority: str = "medium") -> Action:
    return {"action": text, "priority": priority}


DIAGNOSTICS: dict[str, dict[str, list]] = {
    TYPE_CELL_VOLTAGE: {
        "causes": [
            _cause("Cell voltage is outside the configured operating window", True),
            _cause("Cell degradation or imbalance vs. the rest of the pack", False),
            _cause("Sensor or wiring fault on the affected cell", False),
            _cause("Over/under-charge from the charger", False),
        ],
        "actions": [
            _action("Verify the reading against a reference meter", priority="high"),
            _action("Inspect the affected cell and its sense wiring"),
            _action("Re-balance or replace the cell if confirmed"),
        ],
    },
    TYPE_CELL_SPREAD: {
        "causes": [
            _cause("Cell-to-cell voltage spread exceeds the configured limit", True),
            _cause("Weak or aged cell deviating from the pack", False),
            _cause("Poor balancing / BMS balancing disabled", False),
        ],
        "actions": [
            _action("Run a balance cycle and re-check the spread", priority="high"),
            _action("Identify the outlier cell over several cycles"),
            _action("Schedule cell replacement if the spread persists"),
        ],
    },
    TYPE_TEMPERATURE: {
        "causes": [
            _cause("Temperature is outside the configured operating range", True),
            _cause("High load / fast charge generating heat", False),
            _cause("Cooling blocked or ambient too high", False),
            _cause("Sensor fault reporting a spurious temperature", False),
        ],
        "actions": [
            _action("Reduce load or pause charging until temperature normalises", priority="high"),
            _action("Check cooling path and ambient conditions"),
            _action("Verify the temperature sensor calibration"),
        ],
    },
    TYPE_VOLTAGE_JUMP: {
        "causes": [
            _cause("Cell voltage changed faster than the configured limit", True),
            _cause("Intermittent contact or connection fault", False),
            _cause("Load step or switching transient", False),
        ],
        "actions": [
            _action("Inspect terminals and connectors for looseness"),
            _action("Correlate the jump with the current step"),
        ],
    },
    TYPE_CURRENT_JUMP: {
        "causes": [
            _cause("Current changed faster than the configured limit", True),
            _cause("Load or charger switched abruptly", False),
            _cause("Current sensor or shunt fault", False),
        ],
        "actions": [
            _action("Confirm the load/charger step timing"),
            _action("Verify the current sensor / shunt"),
        ],
    },
    TYPE_TELEMETRY_GAP: {
        "causes": [
            _cause("Gap between consecutive samples exceeds the expected interval", True),
            _cause("Connectivity loss between device and broker", False),
            _cause("Device reset or power cycle", False),
            _cause("Backpressure dropped samples at the gateway", False),
        ],
        "actions": [
            _action("Check device connectivity and uptime", priority="high"),
            _action("Review ingestion rejection/quarantine counts for the window"),
        ],
    },
    TYPE_STALE: {
        "causes": [
            _cause("No telemetry received inside the configured window", True),
            _cause("Device offline or powered down", False),
            _cause("Network/MQTT session dropped", False),
        ],
        "actions": [
            _action("Confirm device power and network", priority="high"),
            _action("Check broker connectivity for the device"),
        ],
    },
}


def diagnose(anomaly_type: str) -> tuple[list[Cause], list[Action]]:
    """Return ``(likely_causes, recommended_actions)`` for a rule code.

    Empty lists for an unknown type: the CR forbids inventing explanations
    where no mapping exists.
    """
    entry = DIAGNOSTICS.get(anomaly_type)
    if not entry:
        return [], []
    return list(entry["causes"]), list(entry["actions"])


@dataclass
class Detection:
    """One rule firing from an evaluation pass."""

    anomaly_type: str
    severity: str
    observed_value: float | None
    expected_value: float | None
    threshold: float | None
    affected_cell: int | None
    evidence: dict = field(default_factory=dict)

    def causes(self) -> list[Cause]:
        return diagnose(self.anomaly_type)[0]

    def actions(self) -> list[Action]:
        return diagnose(self.anomaly_type)[1]


def severity_for(*, ratio: float, warn: float = 1.0, crit: float = 1.5) -> str:
    """Map an exceedance ratio to a severity.

    ``ratio`` is ``observed / threshold`` (or the inverse for lower bounds),
    so a value 1.5x beyond the limit is critical and anything past the limit
    is at least a warning.
    """
    if ratio >= crit:
        return SEVERITY_CRITICAL
    if ratio >= warn:
        return SEVERITY_WARNING
    return SEVERITY_INFO


def finite(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def sample_evidence(sample: TelemetrySample, **extra: Any) -> dict[str, Any]:
    """The common evidence block: the reading the rule judged plus extras."""
    return {
        "measured_at": sample.measured_at.isoformat(),
        "pack_voltage": finite(sample.pack_voltage),
        "current": finite(sample.current),
        "temperature": finite(sample.temperature),
        "cell_voltages": [finite(v) for v in (sample.cell_voltages or [])],
        **extra,
    }
