"""Opaque keyset-pagination cursors for time-series reads.

A cursor pins a position in ``(measured_at, id)`` order so the next page can be
fetched without OFFSET, which would rescan everything already returned and
drift when new samples arrive between requests.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime


class InvalidCursor(ValueError):
    """Raised when an opaque cursor cannot be decoded."""


def encode_cursor(measured_at: datetime, sample_id: int) -> str:
    raw = json.dumps({"m": measured_at.isoformat(), "i": sample_id}).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str) -> tuple[datetime, int]:
    try:
        padded = token + "=" * (-len(token) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        measured_at = datetime.fromisoformat(data["m"])
        sample_id = int(data["i"])
    except Exception as exc:  # noqa: BLE001 - any malformed token is a 400
        raise InvalidCursor("cursor is not a valid pagination token") from exc
    if measured_at.tzinfo is None:
        raise InvalidCursor("cursor timestamp is missing a timezone")
    return measured_at, sample_id
