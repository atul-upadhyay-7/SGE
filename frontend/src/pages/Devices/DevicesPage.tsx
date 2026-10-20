/**
 * Device Management page (PRD §4.8).
 *
 * Lists registered devices with their identity, configured cell count, expected
 * sampling rate and last-seen time, and supports registration and viewing
 * details. All validation is the backend's (PRD §4.8); this page surfaces the
 * backend's error message rather than duplicating rules.
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
import { usePolling } from "@/hooks/usePolling";
import { createDevice, listDevices, type DeviceCreate } from "@/services/devices";
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
            <table className="table">
              <thead>
                <tr>
                  <th>Device ID</th>
                  <th>Name</th>
                  <th>Cells</th>
                  <th>Rate (Hz)</th>
                  <th>Chemistry</th>
                  <th>Status</th>
                  <th>Last seen</th>
                </tr>
              </thead>
              <tbody>
                {data.map((device) => (
                  <tr key={device.device_id}>
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
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

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
