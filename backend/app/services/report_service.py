"""Report generation from persisted data (CR §8, PRD §4.7).

A report is assembled once, here, from the same repositories the live APIs read
(telemetry, cycles/SoH, anomalies, actions) and stored as a JSON snapshot. It
never reads from the raw telemetry stream and never runs a model. Large
snapshots are synchronous and still require a queued-job path before
high-volume deployment.

The sections map onto the PRD's report contents:

* core (always): executive summary, period/device details, data availability
* ``telemetry``: voltage/current/temperature summary
* ``soh``: SoH prediction history
* ``anomalies``: counts by severity/type, hourly frequency, event list,
  and the unresolved subset
* ``actions``: recorded actions and resolution outcomes
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.core.config import get_settings
from app.db.models import Device
from app.repositories.anomaly_repository import AnomalyRepository
from app.repositories.cycle_repository import CycleRepository
from app.repositories.model_repository import ModelRepository
from app.repositories.telemetry_repository import TelemetryRepository
from app.schemas.report import ReportCreate
from app.services import anomaly_rules

FORMAT_VERSION = "report-v2"
MAX_REPORT_ROWS = 10_000


def _check_report_size(count: int) -> None:
    if count > MAX_REPORT_ROWS:
        raise HTTPException(
            status_code=422,
            detail=("Report exceeds 10,000 rows; narrow the time range or filters. "
                    "Queued large-report generation is not available yet."),
        )

# The primary tabular section each report renders as CSV: the detailed anomaly
# event list is the most useful "raw tabular export" (PRD §4.7 export formats).
CSV_TABLE = "anomalies"

_ACTION_COLUMNS = (
    "id",
    "detected_at",
    "last_detected_at",
    "anomaly_type",
    "severity",
    "affected_cell",
    "status",
    "observed_value",
    "expected_value",
    "threshold",
    "duration_seconds",
)


class ReportService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def build(self, request: ReportCreate, device: Device | None) -> dict:
        """Assemble the full report payload for the given request.

        ``device`` is the resolved device row (or ``None`` for a fleet-wide
        report); the caller resolves and 404s it, so this method only renders.
        """
        start, end = _as_utc(request.from_), _as_utc(request.to)
        sections = request.sections
        enabled = sections.enabled_names()

        payload: dict = {
            "format_version": FORMAT_VERSION,
            "report_type": request.report_type,
            "generated_at": datetime.now(UTC).isoformat(),
            "period": {
                "from": start.isoformat() if start else None,
                "to": end.isoformat() if end else None,
            },
            "filters": _filters(request),
            "included_sections": enabled,
        }

        payload["device"] = _device_block(device)
        payload["data_availability"] = await self._availability(device, start, end)

        if sections.telemetry and device is not None:
            payload["telemetry"] = await self._telemetry(device, start, end)
        if sections.soh and device is not None:
            payload["soh"] = await self._soh(device, start, end)

        if sections.anomalies:
            payload["anomalies"] = await self._anomalies(device, start, end, request)
        if sections.actions:
            payload["actions"] = await self._actions(device, start, end, request)

        payload["model_metadata"] = await self._model_metadata()
        payload["summary"] = _executive_summary(payload)
        return payload

    # --- sections ---------------------------------------------------------

    async def _availability(
        self, device: Device | None, start: datetime | None, end: datetime | None
    ) -> dict:
        if device is None:
            return {"scope": "fleet"}
        repository = TelemetryRepository(self._session)
        stats = await repository.window_stats(
            device.device_id, start=start, end=end
        )
        rejections = await repository.count_rejections_by_reason(device.device_id)
        return {
            "scope": "device",
            "sample_count": stats["sample_count"],
            "first_measured_at": _iso(stats["first_measured_at"]),
            "last_measured_at": _iso(stats["last_measured_at"]),
            "rejection_count": sum(rejections.values()),
            "rejections_by_reason": rejections,
        }

    async def _telemetry(
        self, device: Device, start: datetime | None, end: datetime | None
    ) -> dict:
        stats = await TelemetryRepository(self._session).window_stats(
            device.device_id, start=start, end=end
        )
        return {
            "sample_count": stats["sample_count"],
            "pack_voltage": stats["pack_voltage"],
            "current": stats["current"],
            "temperature": stats["temperature"],
        }

    async def _soh(
        self, device: Device, start: datetime | None, end: datetime | None
    ) -> dict:
        predictions = await CycleRepository(self._session).list_predictions(
            device.device_id,
            start=start,
            end=end,
            descending=False,
            limit=MAX_REPORT_ROWS + 1,
        )
        _check_report_size(len(predictions))
        rows = [
            {
                "predicted_at": prediction.predicted_at.isoformat(),
                "predicted_soh": prediction.predicted_soh,
                "measured_soh": prediction.measured_soh,
                "cycle_id": prediction.cycle_id,
                "model_version": prediction.model_version,
                "feature_schema_version": prediction.feature_schema_version,
                "input_quality_status": prediction.input_quality_status,
            }
            for prediction in predictions
        ]
        latest = rows[-1]["predicted_soh"] if rows else None
        return {
            "count": len(rows),
            "latest_predicted_soh": latest,
            "history": rows,
        }

    async def _anomalies(
        self,
        device: Device | None,
        start: datetime | None,
        end: datetime | None,
        request: ReportCreate,
    ) -> dict:
        repository = AnomalyRepository(self._session)
        device_id = device.device_id if device is not None else None
        summary = await repository.summary_rows(
            device_id=device_id, start=start, end=end,
            severity=request.severity, anomaly_type=request.anomaly_type,
        )
        hourly = await repository.hourly_activity(
            device_id=device_id, start=start, end=end,
            severity=request.severity, anomaly_type=request.anomaly_type,
        )
        events, total = await repository.list_events(
            device_id=device_id,
            severity=request.severity,
            anomaly_type=request.anomaly_type,
            start=start,
            end=end,
            limit=MAX_REPORT_ROWS + 1,
            offset=0,
        )
        _check_report_size(total)
        event_rows = [_event_row(event) for event in events]
        unresolved = [row for row in event_rows if row["status"] not in {"resolved", "false_positive"}]
        return {
            "total": summary["total"],
            "status_counts": summary["status_counts"],
            "by_severity": summary["severity_counts"],
            "by_type": summary["type_counts"],
            "hourly": [
                {
                    "hour": bucket["hour"].isoformat(),
                    "total": bucket["total"],
                    "open": bucket["open"],
                    "resolved": bucket["resolved"],
                    "false_positive": bucket.get("false_positive", 0),
                    "by_severity": bucket["by_severity"],
                }
                for bucket in hourly
            ],
            "list_count": total,
            "events": event_rows,
            "unresolved_count": len(unresolved),
            "unresolved": unresolved,
        }

    async def _actions(
        self, device: Device | None, start: datetime | None, end: datetime | None,
        request: ReportCreate,
    ) -> dict:
        repository = AnomalyRepository(self._session)
        device_id = device.device_id if device is not None else None
        events, total = await repository.list_events(
            device_id=device_id, start=start, end=end, limit=MAX_REPORT_ROWS + 1, offset=0,
            severity=request.severity, anomaly_type=request.anomaly_type
        )
        _check_report_size(total)
        actions: list[dict] = []
        for event in events:
            trail = await repository.list_actions(event.id)
            for action in trail:
                actions.append(
                    {
                        "anomaly_id": event.id,
                        "device_id": event.device_id,
                        "anomaly_type": event.anomaly_type,
                        "action_type": action.action_type,
                        "actor": action.actor,
                        "note": action.note,
                        "created_at": action.created_at.isoformat(),
                    }
                )
        return {"count": len(actions), "items": actions}

    async def _model_metadata(self) -> dict:
        models = await ModelRepository(self._session).active_models()
        return {
            "active_models": [
                {
                    "model_name": model.model_name,
                    "model_version": model.model_version,
                    "feature_schema_version": model.feature_schema_version,
                }
                for model in models
            ],
            "rules_version": anomaly_rules.RULES_VERSION,
        }


def _device_block(device: Device | None) -> dict:
    if device is None:
        return {"device_id": None, "name": None, "scope": "fleet"}
    return {
        "device_id": device.device_id,
        "name": device.name,
        "cell_count": device.cell_count,
        "chemistry": device.chemistry,
        "sampling_frequency_hz": device.sampling_frequency_hz,
        "status": device.status,
        "scope": "device",
    }


def _filters(request: ReportCreate) -> dict:
    return {
        "device_id": request.device_id,
        "from": request.from_.isoformat() if request.from_ else None,
        "to": request.to.isoformat() if request.to else None,
        "report_type": request.report_type,
        "severity": request.severity,
        "anomaly_type": request.anomaly_type,
        "sections": request.sections.model_dump(),
    }


def _event_row(event) -> dict:
    return {
        "id": event.id,
        "device_id": event.device_id,
        "detected_at": event.detected_at.isoformat(),
        "last_detected_at": event.last_detected_at.isoformat(),
        "anomaly_type": event.anomaly_type,
        "severity": event.severity,
        "affected_cell": event.affected_cell,
        "status": event.status,
        "resolution_outcome": event.resolution_outcome,
        "observed_value": event.observed_value,
        "expected_value": event.expected_value,
        "threshold": event.threshold,
        "duration_seconds": event.duration_seconds,
        "rule_version": event.rule_version,
        "likely_causes": event.likely_causes,
        "recommended_actions": event.recommended_actions,
    }


def _executive_summary(payload: dict) -> dict:
    anomalies = payload.get("anomalies", {})
    device = payload.get("device", {})
    availability = payload.get("data_availability", {})
    soh = payload.get("soh", {})
    return {
        "device": device.get("device_id"),
        "period": payload.get("period"),
        "samples": availability.get("sample_count"),
        "anomalies_total": anomalies.get("total", 0),
        "anomalies_unresolved": anomalies.get("unresolved_count", 0),
        "latest_predicted_soh": soh.get("latest_predicted_soh"),
    }


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def csv_rows(payload: dict) -> list[dict]:
    """The report's primary tabular section for the CSV export (PRD §4.7).

    Only scalar columns are exported (nested cause/action lists are omitted so
    the CSV stays one row per event and parses cleanly).
    """
    return [
        {column: event.get(column) for column in _ACTION_COLUMNS}
        for event in payload.get("anomalies", {}).get("events", [])
    ]


__all__ = ["ReportService", "csv_rows", "CSV_TABLE", "FORMAT_VERSION"]
