/**
 * Persistent device selector.
 *
 * Loads the device list once and keeps the shared selection in sync. When
 * devices exist and nothing is selected yet, it auto-selects the first so
 * device-scoped pages have something to show immediately. If the list is empty
 * it explains why rather than showing a bare dropdown (PRD §3, §7).
 */
import { useEffect } from "react";

import { useAsync } from "@/hooks/useAsync";
import { useSelectedDevice } from "@/hooks/useSelectedDevice";
import { listDevices } from "@/services/devices";

export function DeviceSelector() {
  const { deviceId, setDeviceId } = useSelectedDevice();
  const { data: devices, loading, error } = useAsync(
    (signal) => listDevices(signal),
    [],
  );

  useEffect(() => {
    if (deviceId === null && devices && devices.length > 0) {
      setDeviceId(devices[0].device_id);
    }
  }, [deviceId, devices, setDeviceId]);

  if (loading) {
    return <span className="device-select device-select--muted">Loading devices…</span>;
  }

  if (error) {
    return <span className="device-select device-select--muted">Devices unavailable</span>;
  }

  if (!devices || devices.length === 0) {
    return (
      <span className="device-select device-select--muted">
        No devices registered
      </span>
    );
  }

  return (
    <label className="device-select">
      <span className="device-select__label">Device</span>
      <select
        value={deviceId ?? ""}
        onChange={(event) => setDeviceId(event.target.value || null)}
      >
        <option value="" disabled>
          Select a device…
        </option>
        {devices.map((device) => (
          <option key={device.device_id} value={device.device_id}>
            {device.name} ({device.device_id})
          </option>
        ))}
      </select>
    </label>
  );
}
