/**
 * Severity and status indicators.
 *
 * Severity is never conveyed by colour alone (PRD §7): each pill carries a
 * text label and an icon glyph so it reads without colour perception.
 */
import type { Severity } from "@/types/domain";

const SEVERITY_LABEL: Record<string, string> = {
  info: "Info",
  warning: "Warning",
  critical: "Critical",
};

const SEVERITY_GLYPH: Record<string, string> = {
  info: "i",
  warning: "!",
  critical: "!!",
};

export function SeverityPill({ severity }: { severity: Severity | string }) {
  const key = String(severity).toLowerCase();
  const label = SEVERITY_LABEL[key] ?? String(severity);
  const glyph = SEVERITY_GLYPH[key] ?? "•";
  return (
    <span className={`pill pill--${key}`}>
      <span aria-hidden="true">{glyph}</span>
      {label}
    </span>
  );
}

const STATUS_LABEL: Record<string, string> = {
  active: "Active",
  inactive: "Inactive",
  retired: "Retired",
  open: "Open",
  acked: "Acknowledged",
  acknowledged: "Acknowledged",
  investigating: "Investigating",
  resolved: "Resolved",
  false_positive: "False positive",
  ready: "Ready",
  failed: "Failed",
  generating: "Generating",
  queued: "Queued",
  running: "Running",
};

export function StatusPill({ status }: { status: string }) {
  const key = String(status).toLowerCase();
  const label = STATUS_LABEL[key] ?? status;
  return <span className={`pill pill--status-${key}`}>{label}</span>;
}
