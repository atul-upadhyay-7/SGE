"""Remaining-Useful-Life estimator for the BMS backend (FR-08).

Answers the question the SoH number alone cannot: *how many discharges are
left before this cell is retired?*

The offline analysis (``src/rul_trajectory.py``) established why the answer
comes from **reference-trajectory matching** rather than a regression model
or a fade-rate extrapolation:

* four cells cannot train a cycle-level RUL regressor that beats a constant;
* NASA capacity curves are convex with a knee, so a line fitted before the
  knee overshoots end-of-life by hundreds of cycles.

What does work is simpler: keep the SoH-versus-discharge curve of every
reference cell that reached the retirement threshold, find where each curve
sat at the cell's current (smoothed) SoH, read off that reference's remaining
discharges, and take the median across references. Units are *discharges*,
which is exactly what the streaming tracker counts.

The estimator imports the offline class rather than reimplementing it, so
serving and evaluation cannot drift. It consumes only the cell's own
predicted SoH history — never its future — and the point estimate
(``remaining()``) is by construction the median of the same distribution the
band is computed from, so the two can never disagree.

Honest limits, carried over from the offline module: four reference cells of
one chemistry and one load. The band is the *spread across references*, not a
calibrated predictive interval; its empirical coverage is measured offline in
``evaluate_loco`` rather than assumed here.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"

if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from rul_trajectory import SMOOTH_WINDOW, TrajectoryRul  # noqa: E402

# The reference curves the offline pipeline exports. Kept under models/ so
# they travel with the repository, exactly like the SoH booster.
DEFAULT_REFERENCE_PATH = _REPO_ROOT / "models" / "rul_reference_curves.json"

# Retirement threshold the whole project uses (src/preprocessing.py).
DEFAULT_THRESHOLD_PCT = 80.0

# Status values the API surfaces. Stable strings, safe to branch on.
STATUS_OK = "ok"
STATUS_RETIRED = "retired"
STATUS_NO_HISTORY = "no_history"
STATUS_NO_REFERENCES = "no_references"

_ESTIMATOR_CACHE: dict[str, TrajectoryRul | None] = {}


class RulUnavailableError(RuntimeError):
    """The reference-curve artifact is missing, so no estimate is possible."""


@dataclass(frozen=True, slots=True)
class RulEstimate:
    """One RUL estimate, with everything needed to explain it."""

    status: str
    rul_discharges: int | None
    p10_discharges: float | None
    p90_discharges: float | None
    threshold_soh_pct: float
    reference_count: int
    history_count: int
    history: tuple[float, ...] = ()


def load_estimator(path: Path | str | None = None) -> TrajectoryRul:
    """Load (and cache) the reference-trajectory estimator.

    Raises :class:`RulUnavailableError` when the artifact is absent, so a
    deployment that forgot ``models/`` fails loudly rather than silently
    reporting an infinite remaining life.
    """
    resolved = Path(path) if path is not None else DEFAULT_REFERENCE_PATH
    key = str(resolved)

    if key not in _ESTIMATOR_CACHE:
        _ESTIMATOR_CACHE[key] = TrajectoryRul.load(resolved)

    estimator = _ESTIMATOR_CACHE[key]
    if estimator is None:
        raise RulUnavailableError(
            f"RUL reference curves not found at {resolved}. "
            "Train/export them with:  python src/train_rul.py"
        )
    return estimator


def estimate_rul(
    recent_soh: Iterable[float],
    *,
    path: Path | str | None = None,
) -> RulEstimate:
    """Estimate remaining discharges from a cell's recent predicted SoH.

    Parameters
    ----------
    recent_soh
        The cell's predicted SoH values, **most recent last**. Only the last
        :data:`SMOOTH_WINDOW` (10) are used for the level, matching the
        offline estimator; longer histories are accepted so the caller does
        not have to trim.
    path
        Optional override for the reference-curve artifact.

    Returns
    -------
    RulEstimate
        ``status`` is one of:

        * ``ok``              — a band and a point estimate were produced;
        * ``retired``         — the smoothed SoH is already at/below the
          threshold, so nothing remains (RUL 0);
        * ``no_history``      — no SoH values were supplied;
        * ``no_references``   — no reference cell ever reached the threshold,
          so there is nothing to match against.
    """
    history = tuple(float(v) for v in recent_soh if v is not None)
    if not history:
        return RulEstimate(
            status=STATUS_NO_HISTORY,
            rul_discharges=None,
            p10_discharges=None,
            p90_discharges=None,
            threshold_soh_pct=DEFAULT_THRESHOLD_PCT,
            reference_count=0,
            history_count=0,
            history=(),
        )

    estimator = load_estimator(path)
    threshold = float(estimator.threshold)

    distribution = estimator.remaining_distribution(list(history))
    if distribution is None:
        return RulEstimate(
            status=STATUS_NO_REFERENCES,
            rul_discharges=None,
            p10_discharges=None,
            p90_discharges=None,
            threshold_soh_pct=threshold,
            reference_count=0,
            history_count=len(history),
            history=history,
        )

    import numpy as np

    p10, p50, p90 = np.percentile(distribution, [10.0, 50.0, 90.0])
    reference_count = len(estimator.refs)

    # Already at/below the threshold: the distribution is all zeros, and the
    # caller should see an explicit "retired" rather than a 0 that looks like
    # a rounding artefact.
    level = float(np.median(history[-SMOOTH_WINDOW:]))
    if level <= threshold:
        return RulEstimate(
            status=STATUS_RETIRED,
            rul_discharges=0,
            p10_discharges=0.0,
            p90_discharges=0.0,
            threshold_soh_pct=threshold,
            reference_count=reference_count,
            history_count=len(history),
            history=history,
        )

    return RulEstimate(
        status=STATUS_OK,
        # The point estimate is the median of the same distribution the band
        # is taken from, so it is exactly what remaining() returns.
        rul_discharges=int(round(float(p50))),
        p10_discharges=float(p10),
        p90_discharges=float(p90),
        threshold_soh_pct=threshold,
        reference_count=reference_count,
        history_count=len(history),
        history=history,
    )


def clear_cache() -> None:
    """Drop the cached estimator (tests and hot-reload tooling)."""
    _ESTIMATOR_CACHE.clear()


__all__ = [
    DEFAULT_REFERENCE_PATH,
    DEFAULT_THRESHOLD_PCT,
    RulEstimate,
    RulUnavailableError,
    STATUS_NO_HISTORY,
    STATUS_NO_REFERENCES,
    STATUS_OK,
    STATUS_RETIRED,
    clear_cache,
    estimate_rul,
    load_estimator,
]
