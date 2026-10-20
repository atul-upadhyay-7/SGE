/**
 * Route table.
 *
 * Eight pages per the PRD (PRD §4). Anomaly Details is a child route of the
 * Anomaly Center so "back" returns to the filtered list.
 */
import { createBrowserRouter, Navigate } from "react-router-dom";

import { AppLayout } from "@/components/layout/AppLayout";
import { AnomalyCenterPage } from "@/pages/Anomalies/AnomalyCenterPage";
import { AnomalyDetailsPage } from "@/pages/Anomalies/AnomalyDetailsPage";
import { BatteryHealthPage } from "@/pages/Health/BatteryHealthPage";
import { DevicesPage } from "@/pages/Devices/DevicesPage";
import { LiveMonitoringPage } from "@/pages/Live/LiveMonitoringPage";
import { OverviewPage } from "@/pages/Overview/OverviewPage";
import { ReportsPage } from "@/pages/Reports/ReportsPage";
import { SystemHealthPage } from "@/pages/SystemHealth/SystemHealthPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppLayout />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "live", element: <LiveMonitoringPage /> },
      { path: "anomalies", element: <AnomalyCenterPage /> },
      { path: "anomalies/:anomalyId", element: <AnomalyDetailsPage /> },
      { path: "health", element: <BatteryHealthPage /> },
      { path: "reports", element: <ReportsPage /> },
      { path: "devices", element: <DevicesPage /> },
      { path: "system", element: <SystemHealthPage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);
