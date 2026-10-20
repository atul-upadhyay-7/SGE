/**
 * Anomaly Center (PRD §4.3).
 *
 * The anomaly APIs (list/summary/hourly) are part of a later backend milestone,
 * so this page states what it will show rather than displaying invented counts.
 */
import { PlaceholderPage } from "@/components/feedback/PlaceholderPage";

export function AnomalyCenterPage() {
  return (
    <PlaceholderPage
      title="Anomaly Center"
      subtitle="Active anomalies, severity distribution and recurrence"
      waitingOn="The anomaly list, summary and hourly APIs are not available in this milestone."
      planned={[
        "Active anomaly list with severity, affected cell and duration",
        "Severity distribution and anomalies-per-hour chart",
        "Recurrence count and root-cause action timeline",
        "Filters by severity, type, cell and time range",
      ]}
    />
  );
}
