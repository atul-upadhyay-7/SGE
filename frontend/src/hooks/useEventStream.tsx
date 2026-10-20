/**
 * A single live event stream shared by the whole app.
 *
 * Opening one ``EventSource`` and fanning it out through context means the
 * connection indicator and any event log observe the *same* stream (and the
 * browser holds one connection, not one per component).
 */
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { openEventStream, type StreamState } from "@/services/stream";
import type { LiveEvent } from "@/types/domain";

const DEFAULT_BUFFER = 100;

interface StreamContextValue {
  events: LiveEvent[];
  state: StreamState;
  lastEventAt: Date | null;
  clear: () => void;
}

const StreamContext = createContext<StreamContextValue | null>(null);

export function StreamProvider({ children }: { children: ReactNode }) {
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [state, setState] = useState<StreamState>("connecting");
  const [lastEventAt, setLastEventAt] = useState<Date | null>(null);
  const bufferRef = useRef(DEFAULT_BUFFER);

  useEffect(() => {
    const connection = openEventStream({
      onState: setState,
      onEvent: (event) => {
        setEvents((current) => [event, ...current].slice(0, bufferRef.current));
        setLastEventAt(new Date());
      },
    });
    return () => connection.close();
  }, []);

  const value = useMemo(
    () => ({ events, state, lastEventAt, clear: () => setEvents([]) }),
    [events, state, lastEventAt],
  );
  return <StreamContext.Provider value={value}>{children}</StreamContext.Provider>;
}

export function useStream(): StreamContextValue {
  const context = useContext(StreamContext);
  if (!context) {
    throw new Error("useStream must be used within a StreamProvider");
  }
  return context;
}
