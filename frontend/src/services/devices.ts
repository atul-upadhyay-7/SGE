/** Device endpoints. */
import { apiFetch } from "@/services/api";
import type { Device } from "@/types/domain";

export interface DeviceCreate {
  device_id: string;
  name: string;
  cell_count: number;
  sampling_frequency_hz?: number;
  chemistry?: string;
  nominal_capacity_ah?: number | null;
  status?: string;
  credential?: string | null;
}

export interface DeviceCredentialUpdate {
  credential: string | null;
}

export function listDevices(signal?: AbortSignal): Promise<Device[]> {
  return apiFetch<Device[]>("/devices", { signal });
}

export function getDevice(deviceId: string, signal?: AbortSignal): Promise<Device> {
  return apiFetch<Device>(`/devices/${encodeURIComponent(deviceId)}`, { signal });
}

/** Registration may require the optional admin key (X-Admin-Key). */
export function createDevice(
  payload: DeviceCreate,
  adminKey?: string,
): Promise<Device> {
  return apiFetch<Device>("/devices", {
    method: "POST",
    body: payload,
    headers: adminKey ? { "X-Admin-Key": adminKey } : undefined,
  });
}

export function updateDeviceCredential(
  deviceId: string,
  payload: DeviceCredentialUpdate,
  adminKey?: string,
): Promise<Device> {
  return apiFetch<Device>(`/devices/${encodeURIComponent(deviceId)}/credential`, {
    method: "PUT",
    body: payload,
    headers: adminKey ? { "X-Admin-Key": adminKey } : undefined,
  });
}
