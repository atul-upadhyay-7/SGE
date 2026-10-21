/**
 * Telemetry data-freshness derivation (PRD §4.2, §6, §7).
 *
 * "A healthy API does not mean the BMS is sending data" (PRD §4.9), so freshness
 * is computed from the newest stored sample against the device's own sampling
 * rate — not from whether a request succeeded. The result drives the stale-data
 * warning and the latest-measurement timestamp shown next to the Grafana panel.
 */
import type { Device, TelemetrySample } from "@/types/domain";

/** A device is stale once no sample has arrived for this many expected periods. */
export const STALE_PERIODS = 5;

export interface Freshness {
  /** Newest sample's ``measured_at`` in ms, or null when there is no sample. */
  measuredAtMs: number | null;
  /** Age of the newest sample in seconds, or null when there is no sample. */
  ageSeconds: number | null;
  /** True when a sample exists and is within the expected reporting window. */
  connected: boolean;
  /** True when a sample exists but is older than the expected window. */
  stale: boolean;
}

function periodSeconds(device: Device | null): number {
  const hz = device?.sampling_frequency_hz ?? 0;
  // Guard against a missing/zero rate; fall back to one sample per minute.
  return hz > 0 ? 1 / hz : 60;
}

export function deriveFreshness(
  sample: TelemetrySample | null,
  device: Device | null,
  now: number = Date.now(),
): Freshness {
  if (!sample) {
    return { measuredAtMs: null, ageSeconds: null, connected: false, stale: false };
  }
  const measuredAtMs = new Date(sample.measured_at).getTime();
  if (Number.isNaN(measuredAtMs)) {
    return { measuredAtMs: null, ageSeconds: null, connected: false, stale: false };
  }
  const ageSeconds = Math.max((now - measuredAtMs) / 1000, 0);
  const windowSeconds = periodSeconds(device) * STALE_PERIODS;
  const connected = ageSeconds <= windowSeconds;
  return { measuredAtMs, ageSeconds, connected, stale: !connected };
}
