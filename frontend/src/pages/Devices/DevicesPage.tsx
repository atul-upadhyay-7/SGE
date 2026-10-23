/**
 * Device Management page (PRD §4.8).
 *
 * Lists registered devices with their identity, configured cell count, expected
 * sampling rate, last-seen time, last received telemetry and recent ingestion
 * errors, and supports registration and a per-device details drill-down. All
 * validation is the backend's (PRD §4.8); this page surfaces the backend's
 * error message rather than duplicating rules.
 */
import { useState } from "react";

import { Card, PageHeader } from "@/components/cards/Cards";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  StaleBadge,
} from "@/components/feedback/Feedback";
import { StatusPill } from "@/components/feedback/Indicators";
import { useAsync } from "@/hooks/useAsync";
import { usePolling } from "@/hooks/usePolling";
import { createDevice, getDevice, listDevices, type DeviceCreate } from "@/services/devices";
import { getLatestTelemetry, getTelemetrySummary } from "@/services/telemetry";
import type { Device } from "@/types/domain";
import { formatDateTime, relativeTime } from "@/utils/format";

const EMPTY_FORM: DeviceCreate = {
  device_id: "",
  name: "",
  cell_count: 3,
  chemistry: "li_ion",
  nominal_capacity_ah: 2.5,
};

export function DevicesPage() {
  const { data, error, loading, lastUpdated, refresh } = usePolling(
    (signal) => listDevices(signal),
    15_000,
  );

  const [form, setForm] = useState<DeviceCreate>(EMPTY_FORM);
  const [adminKey, setAdminKey] = useState("");
  const [credential, setCredential] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setMessage(null);
    try {
      await createDevice(
        { ...form, credential: credential || null },
        adminKey || undefined,
      );
      setMessage(`Registered ${form.device_id}`);
      setForm(EMPTY_FORM);
      setCredential("");
      setAdminKey("");
      refresh();
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "Registration failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="page">
      <PageHeader
        title="Device Management"
        subtitle="Registered devices and ingestion configuration"
        actions={
          <div className="page-header__actions">
            <StaleBadge lastUpdated={lastUpdated} />
            <button type="button" className="btn" onClick={refresh}>
              Refresh
            </button>
          </div>
        }
      />

      <Card title="Registered devices" className="card--wide">
        {loading && !data && <LoadingState />}
        {error && <ErrorState message={error} onRetry={refresh} />}
        {data && data.length === 0 && (
          <EmptyState
            title="No devices registered"
            detail="Register a device below to begin ingesting telemetry."
          />
        )}
        {data && data.length > 0 && (
          <div className="table-wrap">
            <table className="table table--clickable">
              <thead>
                <tr>
                  <th>Device ID</th>
                  <th>Name</th>
                  <th>Cells</th>
                  <th>Rate (Hz)</th>
                  <th>Chemistry</th>
                  <th>Status</th>
                  <th>Last seen</th>
                  <th>Last telemetry</th>
                  <th>Ingestion errors</th>
                </tr>
              </thead>
              <tbody>
                {data.map((device) => (
                  <DeviceRow
                    key={device.device_id}
                    device={device}
                    selected={selectedId === device.device_id}
                    onSelect={() =>
                      setSelectedId((current) =>
                        current === device.device_id ? null : device.device_id,
                      )
                    }
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {selectedId && <DeviceDetails deviceId={selectedId} />}

      <Card title="Register device">
        <form className="form form--grid" onSubmit={handleSubmit}>
          <label>
            Device ID
            <input
              required
              value={form.device_id}
              onChange={(e) => setForm({ ...form, device_id: e.target.value })}
              placeholder="BMS_001"
            />
          </label>
          <label>
            Name
            <input
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Pack 1"
            />
          </label>
          <label>
            Cell count
            <input
              type="number"
              min={1}
              max={64}
              required
              value={form.cell_count}
              onChange={(e) =>
                setForm({ ...form, cell_count: Number(e.target.value) })
              }
            />
          </label>
          <label>
            Chemistry
            <select
              value={form.chemistry}
              onChange={(e) => setForm({ ...form, chemistry: e.target.value })}
            >
              <option value="li_ion">li_ion</option>
              <option value="lifepo4">lifepo4</option>
              <option value="nimh">nimh</option>
              <option value="lead_acid">lead_acid</option>
            </select>
          </label>
          <label>
            Nominal capacity (Ah)
            <input
              type="number"
              step="0.1"
              min={0}
              value={form.nominal_capacity_ah ?? ""}
              onChange={(e) =>
                setForm({
                  ...form,
                  nominal_capacity_ah: e.target.value
                    ? Number(e.target.value)
                    : null,
                })
              }
            />
          </label>
          <label>
            Credential (optional)
            <input
              type="password"
              autoComplete="off"
              value={credential}
              onChange={(e) => setCredential(e.target.value)}
              placeholder="min 8 chars"
            />
          </label>
          <label>
            Admin key (if configured)
            <input
              type="password"
              autoComplete="off"
              value={adminKey}
              onChange={(e) => setAdminKey(e.target.value)}
              placeholder="X-Admin-Key"
            />
          </label>
          <div className="form__actions">
            <button type="submit" className="btn btn--primary" disabled={submitting}>
              {submitting ? "Registering…" : "Register"}
            </button>
          </div>
        </form>
        {message && <p className="form__message">{message}</p>}
      </Card>
    </div>
  );
}

/**
 * A device row plus a lazily-loaded ingestion summary. The summary gives the
 * last-received telemetry time and the per-reason rejection counts (PRD §4.8);
 * it is fetched per row so a slow device never blocks the whole table.
 */
function DeviceRow({
  device,
  selected,
  onSelect,
}: {
  device: Device;
  selected: boolean;
  onSelect: () => void;
}) {
  const summary = useAsync(
    (signal) => getTelemetrySummary(device.device_id, {}, signal),
    [device.device_id],
  );
  const errors = summary.data?.rejection_count ?? null;

  return (
    <tr
      className={`table__row--clickable ${
        selected ? "table__row--selected" : ""
      }`}
      tabIndex={0}
      aria-expanded={selected}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
    >
      <td className="mono">{device.device_id}</td>
      <td>{device.name}</td>
      <td>{device.cell_count}</td>
      <td>{device.sampling_frequency_hz}</td>
      <td>{device.chemistry}</td>
      <td>
        <StatusPill status={device.status} />
      </td>
      <td title={formatDateTime(device.last_seen_at)}>
        {relativeTime(device.last_seen_at)}
      </td>
      <td title={formatDateTime(summary.data?.last_measured_at)}>
        {summary.loading && !summary.data
          ? "…"
          : relativeTime(summary.data?.last_measured_at ?? null)}
      </td>
      <td>
        {summary.error ? (
          <span className="muted">—</span>
        ) : (
          <ErrorCount count={errors} reasons={summary.data?.rejections_by_reason} />
        )}
      </td>
    </tr>
  );
}

function ErrorCount({
  count,
  reasons,
}: {
  count: number | null;
  reasons?: Record<string, number>;
}) {
  if (count === null) return <span className="muted">…</span>;
  if (count === 0) return <span className="pill pill--status-ready">none</span>;
  const top = reasons
    ? Object.entries(reasons).sort((a, b) => b[1] - a[1])[0]
    : undefined;
  const hint = top ? `${top[0]} ×${top[1]}` : undefined;
  return (
    <span className="pill pill--status-failed" title={hint}>
      {count}
      {top && <span className="muted"> · {top[0]}</span>}
    </span>
  );
}

/**
 * Details drill-down for one device: identity/config, ingestion summary with
 * the rejection breakdown by reason, and the last received telemetry sample
 * (PRD §4.8). Each block reads its own endpoint and shows an honest state when
 * there is nothing yet (e.g. a device that has never sent data).
 */
function DeviceDetails({ deviceId }: { deviceId: string }) {
  const detail = useAsync((signal) => getDevice(deviceId, signal), [deviceId]);
  const summary = useAsync(
    (signal) => getTelemetrySummary(deviceId, {}, signal),
    [deviceId],
  );
  const latest = useAsync(
    (signal) => getLatestTelemetry(deviceId, signal),
    [deviceId],
  );

  return (
    <Card
      title={`Device details — ${deviceId}`}
      className="card--wide"
    >
      {detail.loading && !detail.data && <LoadingState />}
      {detail.error && (
        <ErrorState message={detail.error} onRetry={detail.reload} />
      )}
      {detail.data && (
        <section className="report__block">
          <h3 className="report__heading">Identity & configuration</h3>
          <dl className="detail-grid">
            <Info label="Device ID" value={detail.data.device_id} mono />
            <Info label="Name" value={detail.data.name} />
            <Info label="Chemistry" value={detail.data.chemistry} />
            <Info label="Cells" value={detail.data.cell_count} />
            <Info
              label="Expected rate (Hz)"
              value={detail.data.sampling_frequency_hz}
            />
            <Info
              label="Nominal capacity"
              value={
                detail.data.nominal_capacity_ah === null
                  ? "—"
                  : `${detail.data.nominal_capacity_ah} Ah`
              }
            />
            <Info label="Status" value={<StatusPill status={detail.data.status} />} />
            <Info
              label="Registered"
              value={formatDateTime(detail.data.created_at)}
            />
            <Info label="Last seen" value={formatDateTime(detail.data.last_seen_at)} />
          </dl>
        </section>
      )}

      <section className="report__block">
        <h3 className="report__heading">Ingestion</h3>
        {summary.loading && !summary.data && <LoadingState />}
        {summary.error && (
          <ErrorState message={summary.error} onRetry={summary.reload} />
        )}
        {summary.data && (
          <>
            <dl className="detail-grid">
              <Info label="Samples stored" value={summary.data.sample_count} />
              <Info
                label="First sample"
                value={formatDateTime(summary.data.first_measured_at)}
              />
              <Info
                label="Last telemetry"
                value={formatDateTime(summary.data.last_measured_at)}
              />
              <Info
                label="Rejected messages"
                value={
                  summary.data.rejection_count === 0 ? (
                    <span className="pill pill--status-ready">none</span>
                  ) : (
                    <span className="pill pill--status-failed">
                      {summary.data.rejection_count}
                    </span>
                  )
                }
              />
            </dl>
            <RejectionBreakdown reasons={summary.data.rejections_by_reason} />
          </>
        )}
      </section>

      <section className="report__block">
        <h3 className="report__heading">Last received telemetry</h3>
        {latest.loading && !latest.data && <LoadingState />}
        {latest.error && <NoTelemetry />}
        {latest.data && (
          <>
            <dl className="detail-grid">
              <Info
                label="Measured at"
                value={formatDateTime(latest.data.measured_at)}
              />
              <Info
                label="Received at"
                value={formatDateTime(latest.data.received_at)}
              />
              <Info
                label="Pack voltage"
                value={
                  latest.data.pack_voltage === null
                    ? "—"
                    : `${latest.data.pack_voltage.toFixed(2)} V`
                }
              />
              <Info
                label="Current"
                value={`${latest.data.current.toFixed(2)} A`}
              />
              <Info
                label="Temperature"
                value={`${latest.data.temperature.toFixed(2)} °C`}
              />
              <Info label="Cells" value={latest.data.cell_voltages.length} />
            </dl>
            <CellVoltages values={latest.data.cell_voltages} />
          </>
        )}
      </section>
    </Card>
  );
}

function NoTelemetry() {
  return (
    <p className="muted">
      No telemetry has been stored for this device yet.
    </p>
  );
}

function RejectionBreakdown({
  reasons,
}: {
  reasons: Record<string, number>;
}) {
  const entries = Object.entries(reasons).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) {
    return <p className="muted">No rejected messages recorded.</p>;
  }
  return (
    <div className="count-list">
      <h4 className="count-list__title">Rejections by reason</h4>
      <ul className="count-list__items">
        {entries.map(([reason, count]) => (
          <li key={reason} className="count-list__item">
            <span className="mono">{reason}</span>
            <span>{count}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function CellVoltages({ values }: { values: number[] }) {
  if (values.length === 0) return null;
  return (
    <div className="cell-grid">
      {values.map((v, index) => (
        <div key={index} className="cell-chip">
          <span className="cell-chip__label">C{index + 1}</span>
          <span className="cell-chip__value mono">{v.toFixed(3)} V</span>
        </div>
      ))}
    </div>
  );
}

function Info({
  label,
  value,
  mono,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="detail">
      <dt className="detail__label">{label}</dt>
      <dd className={`detail__value ${mono ? "mono" : ""}`}>{value}</dd>
    </div>
  );
}
