/**
 * Historical Reports (PRD §4.6).
 *
 * Report generation/download endpoints land in the reports milestone; this page
 * states what it will offer rather than showing a non-functional button.
 */
import { PlaceholderPage } from "@/components/feedback/PlaceholderPage";

export function ReportsPage() {
  return (
    <PlaceholderPage
      title="Historical Reports"
      subtitle="Generate and download operational reports"
      waitingOn="The report generation and download APIs are not available in this milestone."
      planned={[
        "Generate a report for a device and time range",
        "Report status: generating / ready / failed",
        "Download JSON and CSV exports",
        "History of generated reports",
      ]}
    />
  );
}
