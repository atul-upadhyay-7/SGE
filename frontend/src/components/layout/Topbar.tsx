import { ConnectionStatus } from "@/components/layout/ConnectionStatus";
import { OperatorKeyControl } from "@/components/layout/OperatorKeyControl";
import { DeviceSelector } from "@/components/layout/DeviceSelector";

export function Topbar() {
  return (
    <header className="topbar">
      <div className="topbar__title">
        {/* Brand, not a heading: each page renders its own single <h1>
            via PageHeader, so the document outline has exactly one. */}
        <p>Battery Monitoring</p>
      </div>
      <div className="topbar__controls">
        <DeviceSelector />
        <OperatorKeyControl />
        <ConnectionStatus />
      </div>
    </header>
  );
}
