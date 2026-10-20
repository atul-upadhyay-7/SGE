import type { ReactNode } from "react";

export function Spinner() {
  return <span className="spinner" aria-hidden="true" />;
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="feedback feedback--loading" role="status" aria-live="polite">
      <Spinner />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="feedback feedback--error" role="alert">
      <span className="feedback__icon" aria-hidden="true">
        !
      </span>
      <div className="feedback__body">
        <strong>Something went wrong</strong>
        <p>{message}</p>
      </div>
      {onRetry && (
        <button type="button" className="btn" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({
  title = "No data",
  detail,
  action,
}: {
  title?: string;
  detail?: string;
  action?: ReactNode;
}) {
  return (
    <div className="feedback feedback--empty">
      <strong>{title}</strong>
      {detail && <p>{detail}</p>}
      {action}
    </div>
  );
}

export function StaleBadge({ lastUpdated }: { lastUpdated: Date | null }) {
  if (!lastUpdated) return null;
  return (
    <span className="stale-badge" title={lastUpdated.toISOString()}>
      updated {lastUpdated.toLocaleTimeString()}
    </span>
  );
}
