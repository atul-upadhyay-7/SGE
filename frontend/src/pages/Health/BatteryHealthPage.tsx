/**
 * Battery Health / SoH (PRD §4.5).
 *
 * SoH prediction and RUL endpoints exist, but the analytics view (trend,
 * degradation rate, feature attribution) is the SoH analytics milestone.
 */
import { PlaceholderPage } from "@/components/feedback/PlaceholderPage";

export function BatteryHealthPage() {
  return (
    <PlaceholderPage
      title="Battery Health (SoH)"
      subtitle="State of health, degradation trend and remaining useful life"
      waitingOn="The SoH analytics view is not built in this milestone."
      planned={[
        "Current SoH and measured-vs-predicted comparison",
        "SoH degradation trend across cycles",
        "RUL with uncertainty band",
        "Model version, feature schema and input-quality status",
      ]}
    />
  );
}
