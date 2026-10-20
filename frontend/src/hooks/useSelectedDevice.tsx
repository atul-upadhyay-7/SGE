/**
 * Global device selection.
 *
 * A persistent device selector (PRD §2) needs the chosen device to be readable
 * from every page, so the selection is shared through context instead of each
 * page keeping its own copy. Selection lives in memory; pages resolve the
 * device list themselves.
 */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

interface DeviceSelection {
  deviceId: string | null;
  setDeviceId: (deviceId: string | null) => void;
  clear: () => void;
}

const DeviceContext = createContext<DeviceSelection | null>(null);

export function DeviceProvider({ children }: { children: ReactNode }) {
  const [deviceId, setDeviceIdState] = useState<string | null>(null);
  const setDeviceId = useCallback((value: string | null) => {
    setDeviceIdState(value);
  }, []);
  const clear = useCallback(() => setDeviceIdState(null), []);

  const value = useMemo(
    () => ({ deviceId, setDeviceId, clear }),
    [deviceId, setDeviceId, clear],
  );
  return <DeviceContext.Provider value={value}>{children}</DeviceContext.Provider>;
}

export function useSelectedDevice(): DeviceSelection {
  const context = useContext(DeviceContext);
  if (!context) {
    throw new Error("useSelectedDevice must be used within a DeviceProvider");
  }
  return context;
}
