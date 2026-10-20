import { ConnectionStatus } from "@/components/layout/ConnectionStatus";
import { DeviceSelector } from "@/components/layout/DeviceSelector";

export function Topbar() {
  return (
    <header className="topbar">
      <div className="topbar__title">
        <h1>Battery Monitoring</h1>
      </div>
      <div className="topbar__controls">
        <DeviceSelector />
        <ConnectionStatus />
      </div>
    </header>
  );
}
