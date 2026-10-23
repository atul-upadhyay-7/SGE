import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

/**
 * Primary navigation.
 *
 * Icons are inline stroke SVGs drawn on a 24px grid so they stay
 * crisp at nav size and inherit the link colour; the previous
 * unicode glyphs (▦ ⚠ ❤ ⛭) rendered differently on every platform
 * and made the shell look improvised.
 */
function Icon({ children }: { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="18"
      height="18"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

const ICONS = {
  overview: (
    <Icon>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1.6" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1.6" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1.6" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1.6" />
    </Icon>
  ),
  live: (
    <Icon>
      <polyline points="2.5 12 7.5 12 10.5 5 13.8 19 16.8 12 21.5 12" />
    </Icon>
  ),
  anomalies: (
    <Icon>
      <path d="M12 3.6 2.8 19.2a1.6 1.6 0 0 0 1.4 2.3h15.6a1.6 1.6 0 0 0 1.4-2.3L12 3.6z" />
      <line x1="12" y1="9.5" x2="12" y2="13.8" />
      <line x1="12" y1="16.9" x2="12.01" y2="16.9" />
    </Icon>
  ),
  health: (
    <Icon>
      <path d="M12 20.4C6.9 16.6 3.4 13.4 3.4 9.7 3.4 7.1 5.4 5.1 7.9 5.1c1.7 0 3.2.9 4.1 2.3.9-1.4 2.4-2.3 4.1-2.3 2.5 0 4.5 2 4.5 4.6 0 3.7-3.5 6.9-8.6 10.7z" />
      <polyline points="6.2 12 9.3 12 11 9.4 13.2 14 14.6 12 17.8 12" />
    </Icon>
  ),
  validation: (
    <Icon>
      <path d="M9 12.2 11.2 14.4 15.4 9.8" />
      <circle cx="12" cy="12" r="8.6" />
    </Icon>
  ),
  reports: (
    <Icon>
      <path d="M6.5 3.5H14L18.5 8v12a1.5 1.5 0 0 1-1.5 1.5H6.5A1.5 1.5 0 0 1 5 20V5A1.5 1.5 0 0 1 6.5 3.5z" />
      <path d="M13.8 3.5V8.2h4.7" />
      <line x1="8.6" y1="12.6" x2="15.4" y2="12.6" />
      <line x1="8.6" y1="16" x2="15.4" y2="16" />
    </Icon>
  ),
  devices: (
    <Icon>
      <rect x="6" y="6" width="12" height="12" rx="2" />
      <rect x="10" y="10" width="4" height="4" rx="1" />
      <path d="M9 6V3.5M15 6V3.5M9 20.5V18M15 20.5V18M6 9H3.5M6 15H3.5M20.5 9H18M20.5 15H18" />
    </Icon>
  ),
  system: (
    <Icon>
      <path d="M4.2 15.5a8.4 8.4 0 1 1 15.6 0" />
      <line x1="12" y1="15.5" x2="15.6" y2="10" />
      <circle cx="12" cy="15.5" r="1.4" />
    </Icon>
  ),
};

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Overview", icon: ICONS.overview },
  { to: "/live", label: "Live Monitoring", icon: ICONS.live },
  { to: "/anomalies", label: "Anomaly Center", icon: ICONS.anomalies },
  { to: "/health", label: "Battery Health", icon: ICONS.health },
  { to: "/validation", label: "Model Validation", icon: ICONS.validation },
  { to: "/reports", label: "Historical Reports", icon: ICONS.reports },
  { to: "/devices", label: "Device Management", icon: ICONS.devices },
  { to: "/system", label: "System Health", icon: ICONS.system },
];

export function Sidebar() {
  return (
    <nav className="sidebar" aria-label="Primary">
      <div className="sidebar__brand">
        <span className="sidebar__logo" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="18" height="18" focusable="false">
            <path
              fill="currentColor"
              d="M13.2 2.3 4.9 13.4a.6.6 0 0 0 .5.9h5.4l-1 6.6c-.1.5.5.8.9.4l8.4-11a.6.6 0 0 0-.5-.9h-5.4l1-6.6c.1-.5-.6-.8-1-.5z"
            />
          </svg>
        </span>
        <span className="sidebar__brand-text">
          <span className="sidebar__brand-name">BMS Monitor</span>
          <span className="sidebar__brand-caption">Battery Operations</span>
        </span>
      </div>
      <p className="sidebar__section" aria-hidden="true">
        Monitoring
      </p>
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
