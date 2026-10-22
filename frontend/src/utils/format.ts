/**
 * Formatting helpers.
 *
 * Timestamps from the backend are UTC ISO strings. The PRD requires every
 * timestamp to be clearly labelled with its timezone (PRD §7), so the formatter
 * appends the timezone name and the relative helper stays short.
 */

export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return "—";
  const datePart = date.toLocaleDateString();
  const timePart = date.toLocaleTimeString();
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return `${datePart} ${timePart} (${zone})`;
}

export function relativeTime(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return "—";
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

/** Format an ISO span in seconds as a compact human duration. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

/** Value for an ``<input type="datetime-local">`` from a UTC ISO string (local view). */
export function toDatetimeLocal(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate(),
  )}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** Convert a ``datetime-local`` value (local time) to a UTC ISO string. */
export function fromDatetimeLocal(value: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;
  return date.toISOString();
}

/** Label an hour bucket as ``HH:00`` with the local timezone name (PRD §4.5, §7). */
export function formatHourBucket(value: string): string {
  // Hourly API buckets are naive UTC. Date.parse otherwise treats them as
  // local wall time and shifts counts by the browser offset.
  const utc = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(utc);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** The browser's IANA timezone name, for labelling grouped/relative times. */
export function localZoneName(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

/** Short ``HH:MM`` label for a chart x-axis tick (timestamp in ms). */
export function formatAxisTime(value: number): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(1)}%`;
}
