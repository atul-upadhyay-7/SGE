/**
 * Live connection indicator.
 *
 * Reports the actual SSE state (connecting/open/error/closed) with a text label
 * in addition to the dot, so "offline" is never implied by colour alone
 * (PRD §2, §7).
 */
import { useStream } from "@/hooks/useEventStream";

const LABEL: Record<string, string> = {
  connecting: "Connecting…",
  open: "Live",
  error: "Reconnecting…",
  closed: "Offline",
};

export function ConnectionStatus() {
  const { state, lastEventAt } = useStream();
  const label = LABEL[state] ?? state;
  return (
    <span className={`conn conn--${state}`} title={lastEventAt?.toISOString()}>
      <span className="conn__dot" aria-hidden="true" />
      <span className="conn__label">{label}</span>
    </span>
  );
}
