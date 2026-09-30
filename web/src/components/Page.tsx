import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { activeFilterCount, type FacetCounts, type Filters } from "../lib/filters.ts";
import type { Route } from "../lib/routes.ts";
import { useMediaQuery, WIDE_QUERY } from "../state/hooks.ts";
import { applyFilters, FilterPanel, VibeStrip } from "./FilterPanel.tsx";
import { Icon } from "./Icon.tsx";
import { useSidebarSlot } from "./Shell.tsx";
import { Sheet } from "./Sheet.tsx";

export interface Filterable {
  route: Route;
  filters: Filters;
  counts: FacetCounts;
  /** How many events the current filters leave, shown on the phone sheet's button. */
  resultCount: number;
}

function SearchBox({ filterable, autoFocus, onClose }: { filterable: Filterable; autoFocus?: boolean; onClose?: () => void }) {
  const { route, filters } = filterable;
  const [text, setText] = useState(filters.q);
  const latest = useRef(filters);
  latest.current = filters;

  useEffect(() => setText(filters.q), [filters.q]);
  useEffect(() => {
    if (text === latest.current.q) return;
    const id = window.setTimeout(() => applyFilters(route, { ...latest.current, q: text }), 250);
    return () => window.clearTimeout(id);
  }, [text, route]);

  return (
    <form className="search" role="search" onSubmit={(e) => { e.preventDefault(); applyFilters(route, { ...filters, q: text }); }}>
      <Icon name="search" size={18} />
      <input type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder="Search events, hosts, places"
        aria-label="Search events" autoFocus={autoFocus} enterKeyHint="search"
        onKeyDown={(e) => { if (e.key === "Escape" && onClose) onClose(); }} />
      {text && (
        <button type="button" className="icon-button small" aria-label="Clear search"
          onClick={() => { setText(""); applyFilters(route, { ...filters, q: "" }); }}>
          <Icon name="close" size={16} />
        </button>
      )}
    </form>
  );
}

/** Lays out a screen: a header with its title and controls and, on list views, search and filters. */
export function Page({ title, eyebrow, controls, filterable, children, className = "" }: {
  title: ReactNode;
  eyebrow?: ReactNode;
  controls?: ReactNode;
  filterable?: Filterable;
  children: ReactNode;
  className?: string;
}) {
  const wide = useMediaQuery(WIDE_QUERY);
  const slot = useSidebarSlot();
  const root = useRef<HTMLDivElement>(null);
  const header = useRef<HTMLElement>(null);

  // Day headings stick just below the page header, whose height depends on the controls shown.
  useEffect(() => {
    const node = header.current;
    if (!node || !root.current) return;
    const observer = new ResizeObserver(() => root.current?.style.setProperty("--header-h", `${node.offsetHeight}px`));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  const [sheet, setSheet] = useState(false);
  const [searching, setSearching] = useState(false);
  const count = filterable ? activeFilterCount(filterable.filters) : 0;
  const showSearch = filterable && (wide || searching || !!filterable.filters.q);

  useEffect(() => {
    if (typeof title === "string") document.title = `${title} · Events`;
  }, [title]);

  return (
    <div className={`page ${className}`} ref={root}>
      <header className="page-header" ref={header}>
        <div className="page-title-row">
          <div className="page-title">
            {eyebrow && <div className="eyebrow">{eyebrow}</div>}
            <h1 className={controls ? "single-line" : undefined}>{title}</h1>
          </div>
          <div className="page-actions">
            {controls}
            {filterable && !wide && (
              <>
                <button type="button" className={`icon-button${showSearch ? " on" : ""}`} aria-label="Search" aria-expanded={!!showSearch}
                  onClick={() => setSearching((s) => !s)}>
                  <Icon name="search" />
                </button>
                <button type="button" className="icon-button with-badge" aria-label={count ? `Filters, ${count} on` : "Filters"}
                  onClick={() => setSheet(true)}>
                  <Icon name="sliders" />
                  {count > 0 && <span className="badge-dot">{count}</span>}
                </button>
              </>
            )}
          </div>
        </div>
        {showSearch && filterable && <SearchBox filterable={filterable} autoFocus={!wide && searching} onClose={() => setSearching(false)} />}
        {filterable && !wide && <VibeStrip route={filterable.route} filters={filterable.filters} counts={filterable.counts} />}
      </header>
      <div className="page-body">{children}</div>
      {filterable && wide && slot && createPortal(
        <FilterPanel route={filterable.route} filters={filterable.filters} counts={filterable.counts} />, slot)}
      {filterable && !wide && sheet && (
        <Sheet label="Filters" side="bottom" onClose={() => setSheet(false)}
          footer={<button type="button" className="button primary wide" onClick={() => setSheet(false)}>
            Show {filterable.resultCount.toLocaleString()} {filterable.resultCount === 1 ? "event" : "events"}
          </button>}>
          <h2 className="sheet-title">Filters</h2>
          <FilterPanel route={filterable.route} filters={filterable.filters} counts={filterable.counts} showVibes />
        </Sheet>
      )}
    </div>
  );
}
