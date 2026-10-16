"""In-memory device registry used on the ingestion hot path (FR-01).

The MQTT consumer must decide whether a device is known without awaiting a
database round trip. The cache is loaded once at startup and kept in sync
when a device is registered. Any device absent from the cache is treated as
unknown and quarantined (FR-04), so a stale cache can only *reject* data,
never admit it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """The subset of a device the validator needs."""

    device_id: str
    cell_count: int
    sampling_frequency_hz: float
    status: str = "active"


class DeviceCache:
    def __init__(self) -> None:
        self._devices: dict[str, DeviceInfo] = {}

    def load(self, devices: Iterable) -> None:
        self._devices = {
            info.device_id: info for device in devices if (info := _to_info(device))
        }

    def add(self, device) -> None:
        info = _to_info(device)
        if info is not None:
            self._devices[info.device_id] = info

    def remove(self, device_id: str) -> None:
        self._devices.pop(device_id, None)

    def get(self, device_id: str) -> DeviceInfo | None:
        return self._devices.get(device_id)

    def __contains__(self, device_id: object) -> bool:
        return device_id in self._devices

    def __len__(self) -> int:
        return len(self._devices)


def _to_info(device: object) -> DeviceInfo | None:
    device_id = getattr(device, "device_id", None)
    cell_count = getattr(device, "cell_count", None)
    if not device_id or cell_count is None:
        return None
    return DeviceInfo(
        device_id=device_id,
        cell_count=cell_count,
        sampling_frequency_hz=float(getattr(device, "sampling_frequency_hz", 1.0)),
        status=getattr(device, "status", "active"),
    )
