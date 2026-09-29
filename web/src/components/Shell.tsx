import { createContext, useContext, useState, type ReactNode } from "react";
import { dayKey, monthKey } from "../lib/dates.ts";
import { href, type Route } from "../lib/routes.ts";
import { useData } from "../state/data.ts";
import { useLocation } from "../state/router.ts";
import { Icon, type IconName } from "./Icon.tsx";
import { Link } from "./Link.tsx";
import { Banners, Toasts } from "./Notices.tsx";
import { SyncSummary } from "./SyncStatus.tsx";

interface NavItem {
  label: string;
  icon: IconName;
  route: Route;
  match: Route["name"][];
  phone: boolean;
}

const NAV: NavItem[] = [
  { label: "Upcoming", icon: "list", route: { name: "agenda", from: null }, match: ["agenda", "day"], phone: true },
  { label: "Month", icon: "grid", route: { name: "month", month: "" }, match: ["month"], phone: true },
  { label: "Saved", icon: "star", route: { name: "saved" }, match: ["saved"], phone: true },
  { label: "Calendars", icon: "calendars", route: { name: "calendars" }, match: ["calendars", "calendar"], phone: false },
  { label: "Sources", icon: "settings", route: { name: "sources", section: null }, match: ["sources"], phone: true },
];

/** The sidebar slot where the current page puts its filters on wide screens. */
const SlotContext = createContext<HTMLElement | null>(null);
export const useSidebarSlot = () => useContext(SlotContext);

function navHref(item: NavItem, keepFilters: boolean, filters: ReturnType<typeof useLocation>["filters"]): string {
  const route = item.route.name === "month" ? { name: "month" as const, month: monthKey(new Date()) } : item.route;
  return href(route, keepFilters ? filters : undefined);
}

export function Shell({ children, activeRoute }: { children: ReactNode; activeRoute: Route }) {
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const { filters } = useLocation();
  const route = activeRoute;
  const { catalog } = useData();
  const savedCount = catalog?.events.filter((e) => (e.starred || e.going) && Date.parse(e.end_at || e.start_at) > Date.now()).length ?? 0;
  const active = (item: NavItem) => item.match.includes(route.name);
  // Filters carry between list views, so switching Upcoming ↔ Month keeps the same vibe.
  const keep = ["agenda", "day", "month"].includes(route.name);

  return (
    <SlotContext.Provider value={slot}>
      <a className="skip-link" href="#main">Skip to content</a>
      <div className="shell">
        <aside className="sidebar" aria-label="Navigation and filters">
          <Link to={href({ name: "agenda", from: null })} className="brand">
            <img src="icon.svg" alt="" width={28} height={28} />
            <span>Events</span>
          </Link>
          <nav className="side-nav" aria-label="Main">
            {NAV.map((item) => (
              <Link key={item.label} to={navHref(item, keep && item.match.some((m) => ["agenda", "month"].includes(m)), filters)}
                className={active(item) ? "active" : ""} aria-current={active(item) ? "page" : undefined}>
                <Icon name={item.icon} filled={item.icon === "star" && active(item)} />
                <span>{item.label}</span>
                {item.label === "Saved" && savedCount > 0 && <span className="nav-count">{savedCount}</span>}
              </Link>
            ))}
          </nav>
          <div className="sidebar-slot" ref={setSlot} />
          <div className="sidebar-foot"><SyncSummary /></div>
        </aside>
        <div className="main-column">
          <Banners />
          <main id="main" tabIndex={-1}>{children}</main>
        </div>
        <nav className="tab-bar" aria-label="Main">
          {NAV.filter((n) => n.phone).map((item) => (
            <Link key={item.label} to={navHref(item, keep && item.match.some((m) => ["agenda", "month"].includes(m)), filters)}
              className={active(item) ? "active" : ""} aria-current={active(item) ? "page" : undefined}>
              <Icon name={item.icon} filled={item.icon === "star" && active(item)} size={22} />
              <span>{item.label}</span>
            </Link>
          ))}
        </nav>
      </div>
      <Toasts />
    </SlotContext.Provider>
  );
}

export const todayKey = () => dayKey(new Date());
