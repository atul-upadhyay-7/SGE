/**
 * A button that turns a consequential action into a two-step confirm.
 *
 * Irreversible actions (resolving an anomaly, registering a device) must be
 * confirmed (PRD §7). Inline confirmation is used instead of ``window.confirm``
 * because it is styleable, keyboard-accessible and testable: the first click
 * arms the button, and it reverts after a short timeout if not confirmed.
 */
import { useEffect, useRef, useState } from "react";

export interface ConfirmButtonProps {
  label: string;
  confirmLabel: string;
  onConfirm: () => void;
  disabled?: boolean;
  className?: string;
  title?: string;
  /** Seconds to wait before disarming. */
  timeoutMs?: number;
}

export function ConfirmButton({
  label,
  confirmLabel,
  onConfirm,
  disabled = false,
  className = "btn",
  title,
  timeoutMs = 4000,
}: ConfirmButtonProps) {
  const [armed, setArmed] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, []);

  function disarm() {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    setArmed(false);
  }

  function handleClick() {
    if (disabled) return;
    if (!armed) {
      setArmed(true);
      timer.current = window.setTimeout(disarm, timeoutMs);
      return;
    }
    disarm();
    onConfirm();
  }

  return (
    <button
      type="button"
      className={armed ? `${className} btn--danger` : className}
      disabled={disabled}
      title={title}
      aria-live="polite"
      onClick={handleClick}
      onBlur={disarm}
    >
      {armed ? confirmLabel : label}
    </button>
  );
}
