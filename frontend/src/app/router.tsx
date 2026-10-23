/**
 * Route table.
 *
 * Eight pages per the PRD (PRD §4). Anomaly Details is a child route of the
 * Anomaly Center so "back" returns to the filtered list.
 *
 * Pages are loaded lazily (one chunk per page) instead of being bundled
 * into a single entry chunk: the operator who opens Overview should not
 * have to download Reports and Anomaly Details up front. The Suspense
 * boundary lives in AppLayout around the <Outlet />.
 */
import { lazy } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";

import { AppLayout } from "@/components/layout/AppLayout";

const OverviewPage = lazy(() =>
  import("@/pages/Overview/OverviewPage").then((m) => ({
    default: m.OverviewPage,
  })),
);
const LiveMonitoringPage = lazy(() =>
  import("@/pages/Live/LiveMonitoringPage").then((m) => ({
    default: m.LiveMonitoringPage,
  })),
);
const AnomalyCenterPage = lazy(() =>
  import("@/pages/Anomalies/AnomalyCenterPage").then((m) => ({
    default: m.AnomalyCenterPage,
  })),
);
const AnomalyDetailsPage = lazy(() =>
  import("@/pages/Anomalies/AnomalyDetailsPage").then((m) => ({
    default: m.AnomalyDetailsPage,
  })),
);
const BatteryHealthPage = lazy(() =>
  import("@/pages/Health/BatteryHealthPage").then((m) => ({
    default: m.BatteryHealthPage,
  })),
);
const ReportsPage = lazy(() =>
  import("@/pages/Reports/ReportsPage").then((m) => ({
    default: m.ReportsPage,
  })),
);
const DevicesPage = lazy(() =>
  import("@/pages/Devices/DevicesPage").then((m) => ({
    default: m.DevicesPage,
  })),
);
const SystemHealthPage = lazy(() =>
  import("@/pages/SystemHealth/SystemHealthPage").then((m) => ({
    default: m.SystemHealthPage,
  })),
);

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
