"""Model-validation reads (PoC validation slice).

``GET /validation/summary`` serves the latest validation report
written by ``scripts/validate_real_data.py``, which scores discharge
cycles through the live estimator (XGBoost ``soh_window_all`` only).

The report is a file, not a database table: validation is an offline,
deliberate run against an exported dataset, and the dashboard's job
is only to present its result honestly — including when no run
exists yet. A missing report is a 404 with run instructions, never
fabricated metrics. Read access follows the application-wide viewer
guard (see ``app.api.deps.require_read_access``), like every other
GET route.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import Settings, get_settings

router = APIRouter(prefix="/validation", tags=["validation"])

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORT_PATH = (
    _REPO_ROOT / "data" / "processed" / "validation" / "latest_validation.json"
)

_REQUIRED_KEYS = {"generated_at", "source", "dataset", "model", "summary", "rows"}
_REQUIRED_SUMMARY_KEYS = {"scored_rows", "refused_rows", "mae_pct", "rmse_pct"}


def resolve_report_path(settings: Settings) -> Path:
    if settings.validation_report_path:
        return Path(settings.validation_report_path)
    return DEFAULT_REPORT_PATH


@router.get("/summary")
async def validation_summary(
    settings: Settings = Depends(get_settings),
) -> dict:
    path = resolve_report_path(settings)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "no validation run yet: run "
                "scripts/validate_real_data.py --input <cycles.csv> "
                "to produce a report"
            ),
        )
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="validation report exists but could not be read",
        ) from exc
    if (
        not isinstance(report, dict)
        or not _REQUIRED_KEYS.issubset(report)
        or not isinstance(report.get("summary"), dict)
        or not _REQUIRED_SUMMARY_KEYS.issubset(report["summary"])
        or not isinstance(report.get("rows"), list)
    ):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="validation report exists but has an unexpected shape",
        )
    return report
