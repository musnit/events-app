import { useCallback, useMemo, useState } from "react";
import { Avatar } from "../components/Avatar.tsx";
import { EventList } from "../components/EventList.tsx";
import { Icon } from "../components/Icon.tsx";
import { Link } from "../components/Link.tsx";
import { EmptyState } from "../components/Notices.tsx";
import { Page } from "../components/Page.tsx";
import { api } from "../lib/api.ts";
import { filtersToParams } from "../lib/filters.ts";
import { hasEnded, SOURCE_LABEL } from "../lib/format.ts";
import { href } from "../lib/routes.ts";
import type { CalendarItem, EventItem } from "../lib/types.ts";
import { data, useData } from "../state/data.ts";
import { useView } from "../state/view.ts";
import { useFilteredEvents, useNow } from "../state/hooks.ts";
import { navigate } from "../state/router.ts";
import { toast } from "../state/toasts.ts";
import { LoadingCards, NoEvents } from "./common.tsx";

function MuteSwitch({ cal }: { cal: CalendarItem }) {
  return (
    <label className="switch" title={cal.muted ? "Muted: its events are hidden" : "Showing its events"}>
      <input type="checkbox" checked={!cal.muted} onChange={(e) => void data.setMuted(cal.id, !e.target.checked)}
        aria-label={`Show events from ${cal.name}`} />
      <span className="switch-track" aria-hidden="true" />
    </label>
  );
}

type Sort = "name" | "upcoming";

/** Every calendar, with event counts, sync health and a switch to mute it. */
export function CalendarsView() {
  const { catalog, loading } = useData();
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("upcoming");
  const list = useMemo(() => {
    const q = query.trim().toLowerCase();
    const items = (catalog?.calendars ?? []).filter((c) => !q || c.name.toLowerCase().includes(q));
    return [...items].sort((a, b) => sort === "name" ? a.name.localeCompare(b.name) : b.upcoming_count - a.upcoming_count || a.name.localeCompare(b.name));
  }, [catalog, query, sort]);
  const mutedCount = catalog?.calendars.filter((c) => c.muted).length ?? 0;

  return (
    <Page title="Calendars" eyebrow={catalog ? `${catalog.calendars.length} calendars${mutedCount ? ` · ${mutedCount} muted` : ""}` : undefined}>
      <div className="toolbar">
        <div className="search">
          <Icon name="search" size={18} />
          <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a calendar" aria-label="Find a calendar" />
        </div>
        <div className="segmented" role="radiogroup" aria-label="Sort calendars">
          <button type="button" role="radio" aria-checked={sort === "upcoming"} className={sort === "upcoming" ? "on" : ""} onClick={() => setSort("upcoming")}>Most events</button>
          <button type="button" role="radio" aria-checked={sort === "name"} className={sort === "name" ? "on" : ""} onClick={() => setSort("name")}>A–Z</button>
        </div>
      </div>
      {loading && !catalog ? <LoadingCards count={4} /> : list.length ? (
        <>
        <p className="muted small list-hint">Switch a calendar off to mute it: its events disappear from your lists but stay on its own page.</p>
        <ul className="calendar-list">
          {list.map((cal) => (
            <li key={cal.id} className={cal.muted ? "muted-row" : ""}>
              <Avatar src={cal.avatar_url} name={cal.name} size={40} square />
              <div className="calendar-info">
                <Link to={href({ name: "calendar", id: cal.id })} className="strong">{cal.name}</Link>
                <div className="muted small">
                  {SOURCE_LABEL[cal.source]} · {cal.upcoming_count} upcoming
                  {cal.last_error && <span className="error-text"> · sync failed</span>}
                </div>
              </div>
              <MuteSwitch cal={cal} />
            </li>
          ))}
        </ul>
        </>
      ) : (
        <EmptyState title={query ? "No calendar by that name" : "No calendars yet"}>
          {!query && <Link className="button primary" to={href({ name: "sources", section: "luma" })}>Connect Luma</Link>}
        </EmptyState>
      )}
    </Page>
  );
}

/** One calendar: what it is, its upcoming events (anywhere), and controls. */
export function CalendarView({ id }: { id: string }) {
  const { route, filters } = useView();
  const { catalog, loading } = useData();
  const now = useNow();
  const cal = catalog?.calendarsById.get(id);
  const scope = useCallback((ev: EventItem) => ev.calendar_ids.includes(id) && !hasEnded(ev, now), [id, now]);
  const withHidden = useMemo(() => ({ ...filters, hidden: true }), [filters]);
  const { events, counts } = useFilteredEvents(withHidden, { scope, area: "all" });

  if (!cal) {
    return loading || !catalog ? <LoadingCards count={3} /> : (
      <Page title="Calendar not found">
        <EmptyState title="That calendar isn't connected">
          <Link className="button" to={href({ name: "calendars" })}>All calendars</Link>
        </EmptyState>
      </Page>
    );
  }

  // Only calendars added here (import or link) can be removed here; Luma follows and configuration keep theirs.
  const removable = cal.source === "luma" && cal.id.startsWith("cal-") && cal.origins.every((o) => o === "import" || o === "link");

  async function remove() {
    if (!cal || !window.confirm(`Stop pulling ${cal.name}? Its events disappear until you add it again.`)) return;
    try {
      await api.lumaRemoveCalendar(cal.id);
      toast(`Removed ${cal.name}`);
      await data.afterSourcesChanged();
      navigate(href({ name: "calendars" }), { replace: true });
    } catch (e) {
      toast(`Could not remove it: ${(e as Error).message}`, "error");
    }
  }

  return (
    <Page
      title={cal.name}
      eyebrow={`${SOURCE_LABEL[cal.source]} calendar`}
      filterable={{ route, filters, counts, resultCount: events.length }}
    >
      <div className="calendar-header">
        <Avatar src={cal.avatar_url} name={cal.name} size={56} square />
        <div>
          {cal.description && <p>{cal.description}</p>}
          <div className="row-wrap">
            {cal.url && <a className="button small" href={cal.url} target="_blank" rel="noopener noreferrer">Open on {SOURCE_LABEL[cal.source]} <Icon name="external" size={14} /></a>}
            <button type="button" className="button small" onClick={() => void data.setMuted(cal.id, !cal.muted)}>
              <Icon name="bellOff" size={16} /> {cal.muted ? "Unmute" : "Mute"}
            </button>
            {removable && <button type="button" className="button small subtle danger" onClick={() => void remove()}><Icon name="trash" size={16} /> Remove</button>}
          </div>
          {cal.muted && <p className="muted small">Muted: its events are hidden everywhere else in the app.</p>}
          {cal.last_error && <p className="error-text small">Last sync failed: {cal.last_error}</p>}
        </div>
      </div>
      <EventList events={events} resetKey={`${id}|${filtersToParams(filters)}`} empty={<NoEvents route={route} filters={filters} what="upcoming events" />} />
    </Page>
  );
}
