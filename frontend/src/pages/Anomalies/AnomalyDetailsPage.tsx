/**
 * Anomaly Details (PRD §4.4).
 *
 * Full detail for a single anomaly: sensor timeline at the time of detection,
 * diagnosis, recommended and taken actions, and recurrence history — once the
 * anomaly detail/history/action APIs exist.
 */
import { useParams } from "react-router-dom";

import { PlaceholderPage } from "@/components/feedback/PlaceholderPage";

export function AnomalyDetailsPage() {
  const { anomalyId } = useParams<{ anomalyId: string }>();
  return (
    <PlaceholderPage
      title="Anomaly Details"
      subtitle={anomalyId ? `Anomaly #${anomalyId}` : undefined}
      waitingOn="The anomaly detail, history and action APIs are not available in this milestone."
      planned={[
        "Sensor timeline around the detection window",
        "Diagnosis: confirmed vs unconfirmed causes",
        "Recommended actions and the action log",
        "Recurrence history for this anomaly type and cell",
      ]}
    />
  );
}
