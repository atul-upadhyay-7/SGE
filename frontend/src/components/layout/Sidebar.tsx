import { NavLink } from "react-router-dom";

interface NavItem {
  to: string;
  label: string;
  icon: string;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Overview", icon: "▦" },
  { to: "/live", label: "Live Monitoring", icon: "▤" },
  { to: "/anomalies", label: "Anomaly Center", icon: "⚠" },
  { to: "/health", label: "Battery Health", icon: "❤" },
  { to: "/reports", label: "Historical Reports", icon: "▦" },
  { to: "/devices", label: "Device Management", icon: "▣" },
  { to: "/system", label: "System Health", icon: "⛭" },
];

export function Sidebar() {
  return (
    <nav className="sidebar" aria-label="Primary">
      <div className="sidebar__brand">
        <span className="sidebar__logo" aria-hidden="true">
          ⚡
        </span>
        <span>BMS Monitor</span>
      </div>
      <ul className="sidebar__list">
        {NAV_ITEMS.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                isActive ? "sidebar__link sidebar__link--active" : "sidebar__link"
              }
            >
              <span className="sidebar__icon" aria-hidden="true">
                {item.icon}
              </span>
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
