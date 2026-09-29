import { useId, type ReactNode } from "react";
import { activeFilterCount, AREA_CHOICES, EMPTY_FILTERS, TIMES, toggle, type FacetCounts, type Filters } from "../lib/filters.ts";
import { href, type Route } from "../lib/routes.ts";
import type { AreaChoice } from "../lib/types.ts";
import { data, useData } from "../state/data.ts";
import { useArea } from "../state/hooks.ts";
import { navigate } from "../state/router.ts";

export function applyFilters(route: Route, filters: Filters): void {
  navigate(href(route, filters), { replace: true, keepScroll: true });
}

function Chip({ on, onClick, children, count, title }: { on: boolean; onClick: () => void; children: ReactNode; count?: number; title?: string }) {
  const empty = count === 0 && !on;
  return (
    <button type="button" className={`chip${on ? " on" : ""}${empty ? " empty" : ""}`} aria-pressed={on} onClick={onClick} title={title}>
      <span>{children}</span>
      {count !== undefined && <span className="chip-count">{count}</span>}
    </button>
  );
}

function Group({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  const id = useId();
  return (
    <section className="filter-group" aria-labelledby={id}>
      <div className="filter-group-head">
        <h3 id={id}>{title}</h3>
        {action}
      </div>
      <div className="chips">{children}</div>
    </section>
  );
}

/** Vibe chips alone, for the scrollable strip above lists on phones. */
export function VibeStrip({ route, filters, counts }: { route: Route; filters: Filters; counts: FacetCounts }) {
  const { catalog } = useData();
  if (!catalog) return null;
  return (
    <div className="vibe-strip" role="group" aria-label="Filter by vibe">
      <button type="button" className={`chip${filters.vibes.length ? "" : " on"}`} aria-pressed={!filters.vibes.length}
        onClick={() => applyFilters(route, { ...filters, vibes: [] })}>All</button>
      {catalog.vibes.map((v) => (
        <Chip key={v.id} on={filters.vibes.includes(v.id)} count={counts.vibes[v.id] ?? 0} title={v.hint}
          onClick={() => applyFilters(route, { ...filters, vibes: toggle(filters.vibes, v.id) })}>
          {v.emoji} {v.label}
        </Chip>
      ))}
    </div>
  );
}

/** Every filter, for the desktop sidebar and the phone filter sheet. */
export function FilterPanel({ route, filters, counts, showVibes = true }: { route: Route; filters: Filters; counts: FacetCounts; showVibes?: boolean }) {
  const { catalog } = useData();
  const preferred = useArea();
  const set = (patch: Partial<Filters>) => applyFilters(route, { ...filters, ...patch });
  if (!catalog) return null;
  const area = filters.area ?? preferred;
  const active = activeFilterCount(filters) + (filters.q ? 1 : 0);

  function chooseArea(choice: AreaChoice) {
    // Picking an area saves it as the default, so it sticks on every device and view.
    if (filters.area) set({ area: null });
    void data.setArea(choice);
  }

  return (
    <div className="filter-panel">
      {showVibes && (
        <Group title="Vibe">
          {catalog.vibes.map((v) => (
            <Chip key={v.id} on={filters.vibes.includes(v.id)} count={counts.vibes[v.id] ?? 0} title={v.hint}
              onClick={() => set({ vibes: toggle(filters.vibes, v.id) })}>{v.emoji} {v.label}</Chip>
          ))}
        </Group>
      )}
      <Group title="When">
        {TIMES.map((t) => (
          <Chip key={t.id} on={filters.times.includes(t.id)} count={counts.times[t.id] ?? 0} title={t.hint}
            onClick={() => set({ times: toggle(filters.times, t.id) })}>{t.label}</Chip>
        ))}
        <Chip on={filters.weekend} onClick={() => set({ weekend: !filters.weekend })} title="Friday evening to Sunday">Weekend</Chip>
      </Group>
      <Group title="Topic">
        {catalog.topics.map((t) => (
          <Chip key={t.id} on={filters.topics.includes(t.id)} count={counts.topics[t.id] ?? 0} title={t.hint}
            onClick={() => set({ topics: toggle(filters.topics, t.id) })}>{t.emoji} {t.label}</Chip>
        ))}
      </Group>
      <Group title="Where">
        <div className="segmented" role="radiogroup" aria-label="Area">
          {AREA_CHOICES.map((a) => (
            <button key={a.id} type="button" role="radio" aria-checked={area === a.id} className={area === a.id ? "on" : ""}
              onClick={() => chooseArea(a.id)}>{a.label}</button>
          ))}
        </div>
        {area !== "all" && catalog.zones.map((z) => (
          <Chip key={z.id} on={filters.zones.includes(z.id)} count={counts.zones[z.id] ?? 0}
            onClick={() => set({ zones: toggle(filters.zones, z.id) })}>{z.label}</Chip>
        ))}
      </Group>
      <Group title="Crowd">
        {catalog.sizes.map((s) => (
          <Chip key={s.id} on={filters.sizes.includes(s.id)} count={counts.sizes[s.id] ?? 0} title={s.hint}
            onClick={() => set({ sizes: toggle(filters.sizes, s.id) })}>{s.label}</Chip>
        ))}
      </Group>
      <Group title="Only">
        <Chip on={filters.going} onClick={() => set({ going: !filters.going })}>Going</Chip>
        <Chip on={filters.fresh} onClick={() => set({ fresh: !filters.fresh })} title="Announced since your last visit">New</Chip>
        <Chip on={filters.free} onClick={() => set({ free: !filters.free })}>Free</Chip>
        <Chip on={filters.open} onClick={() => set({ open: !filters.open })} title="Hide sold-out events">Not sold out</Chip>
        <Chip on={filters.hidden} onClick={() => set({ hidden: !filters.hidden })} title="Include events you hid and muted calendars">Show hidden</Chip>
      </Group>
      {active > 0 && (
        <button type="button" className="button subtle clear-filters" onClick={() => applyFilters(route, { ...EMPTY_FILTERS })}>
          Clear {active === 1 ? "filter" : `${active} filters`}
        </button>
      )}
    </div>
  );
}
