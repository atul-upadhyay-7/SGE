import { useEffect, useState, type ReactNode } from "react";

import { apiFetch } from "@/services/api";
import {
  AUTH_REQUIRED_EVENT,
  AUTH_RESOLVED_EVENT,
  getViewerKey,
  setViewerKey,
} from "@/services/operatorKey";

interface Probe {
  access_control?: { viewer_key?: boolean };
}

/**
 * Asks for the viewer key when the backend requires one. The bare /health
 * probe is open and says whether a viewer key is configured; any later 401 on
 * a read also raises the prompt (for example after the key was rotated).
 *
 * The prompt is an overlay: the app stays mounted behind it, so a
 * mid-session key rotation no longer discards whatever the operator
 * had typed into a form or a resolution workflow. On unlock, data
 * hooks hear AUTH_RESOLVED_EVENT and reload what the 401 blocked.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [needsKey, setNeedsKey] = useState(false);
  const [value, setValue] = useState("");
  const [rejected, setRejected] = useState(false);

  useEffect(() => {
    const onRequired = () => setNeedsKey(true);
    window.addEventListener(AUTH_REQUIRED_EVENT, onRequired);
    apiFetch<Probe>("/health")
      .then(body => {
        if (body.access_control?.viewer_key && !getViewerKey()) setNeedsKey(true);
      })
      .catch(() => undefined);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, onRequired);
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setViewerKey(value);
    setValue("");
    try {
      await apiFetch("/devices");
      setRejected(false);
      setNeedsKey(false);
      window.dispatchEvent(new Event(AUTH_RESOLVED_EVENT));
    } catch {
      setViewerKey("");
      setRejected(true);
    }
  }

  return (
    <>
      <div style={{ display: "contents" }} aria-hidden={needsKey || undefined}>
        {children}
      </div>
      {needsKey && (
        <main className="auth-gate auth-gate--overlay">
          <form className="card auth-gate__form" onSubmit={submit}>
            <h1>Viewer key required</h1>
            <p className="muted">
              This dashboard needs a key to read battery data. The key stays in this
              browser tab only.
            </p>
            <label htmlFor="viewer-key-input">Viewer key</label>
            <input
              id="viewer-key-input"
              type="password"
              autoComplete="off"
              autoFocus
              value={value}
              onChange={event => setValue(event.target.value)}
            />
            {rejected && <p role="alert">That key was not accepted.</p>}
            <button type="submit" className="btn btn--primary" disabled={!value.trim()}>
              Unlock
            </button>
          </form>
        </main>
      )}
    </>
  );
}
