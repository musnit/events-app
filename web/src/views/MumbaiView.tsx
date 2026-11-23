import { useEffect, useMemo } from "react";
import { Icon } from "../components/Icon.tsx";
import { Link } from "../components/Link.tsx";
import { EmptyState } from "../components/Notices.tsx";
import { Page } from "../components/Page.tsx";
import { ResearchDetail, ResearchProse } from "../components/ResearchDetail.tsx";
import {
  KIND_LABELS, matchesResearch, MUMBAI_COLLECTION, mumbaiEntries, PRIORITY_LABELS, RESEARCH_VIEWS,
  researchDateLabel, researchDay, researchFilters, researchWhen, sortResearch, type ResearchFilters,
} from "../lib/research.ts";
import { href } from "../lib/routes.ts";
import type { ResearchEntry } from "../lib/types.ts";
import { data, useData } from "../state/data.ts";
import { navigate, useLocation } from "../state/router.ts";
import { LoadingCards } from "./common.tsx";

function ResearchCard({ entry }: { entry: ResearchEntry }) {
  const { catalog } = useData();
  const ev = catalog?.events.find((e) => e.research?.id === entry.id || e.research_entries?.some((r) => r.id === entry.id));
  return <article className={`research-card priority-${entry.priority}`}>
    <div className="research-card-labels"><span className={`research-priority ${entry.priority}`}>{PRIORITY_LABELS[entry.priority]}</span><span className="research-kind">{KIND_LABELS[entry.kind]}</span></div>
    <h3><Link to={href({ name: "research", id: entry.id })}>{entry.title}</Link></h3>
    <p className="research-card-when"><Icon name="clock" size={15} />{researchWhen(entry)}</p>
    <p className="research-card-venue"><Icon name="pin" size={15} />{entry.venue || "Venue to be confirmed"}</p>
    <p className="research-card-verdict">{entry.verdict}</p>
    <p className="research-card-summary">{entry.summary}</p>
    {entry.people.length > 0 && <p className="research-card-people">{entry.people.map((p) => p.name).join(" · ")}</p>}
    <div className="research-card-foot">
      <Link to={href({ name: "research", id: entry.id })} className="button subtle">Read the research <Icon name="chevronRight" size={16} /></Link>
      {ev && <button type="button" className={`icon-button${ev.starred ? " on" : ""}`} aria-label={`${ev.starred ? "Unsave" : "Save"} ${entry.title}`} aria-pressed={ev.starred} onClick={() => void data.mark(ev.id, { starred: !ev.starred })}><Icon name="star" filled={ev.starred} /></button>}
    </div>
  </article>;
}

export function MumbaiView() {
  const { catalog, loading } = useData();
  const { search } = useLocation();
  const collection = catalog?.researchCollections.find((c) => c.id === MUMBAI_COLLECTION && c.city.toLowerCase() === "mumbai");
  const entries = useMemo(() => mumbaiEntries(catalog?.researchCollections ?? []), [catalog]);
  const filters = useMemo(() => researchFilters(new URLSearchParams(search)), [search]);
  const shown = useMemo(() => entries.filter((e) => matchesResearch(e, filters)).sort(sortResearch), [entries, filters]);
  const days = [...new Set(entries.map(researchDay).filter((d): d is string => !!d))].sort();
  const set = (patch: Partial<ResearchFilters>) => {
    const next = { ...filters, ...patch };
    const params = new URLSearchParams();
    if (next.view !== "curated") params.set("view", next.view);
    if (next.q) params.set("q", next.q);
    if (next.day) params.set("date", next.day);
    if (next.kind) params.set("kind", next.kind);
    if (next.priority) params.set("priority", next.priority);
    navigate(`mumbai${params.size ? `?${params}` : ""}`, { replace: true, keepScroll: true });
  };
  const countFor = (view: ResearchFilters["view"]) => entries.filter((e) => matchesResearch(e, { q: "", day: "", kind: "", priority: "", view })).length;

  return <Page title="Mumbai" eyebrow="Devcon week · 2026" className="mumbai-page">
    {loading && !catalog ? <LoadingCards /> : !collection ? <EmptyState title="Mumbai research is on its way"><p>The collection has not been loaded yet. Try refreshing in a moment.</p><button className="button" onClick={() => void data.poll()}>Refresh</button></EmptyState> : <>
      <section className="research-hero" aria-labelledby="research-heading">
        <div className="research-hero-top"><span className="research-edition">A field guide for the curious</span><span>Checked {researchDateLabel(collection.checked_at)}</span></div>
        <h2 id="research-heading">{collection.title}</h2>
        <ResearchProse text={collection.intro} />
        <div className="research-hero-bottom"><span><strong>{entries.length}</strong> researched picks & leads</span><span>All times in IST · Mumbai only</span></div>
      </section>
      {(collection.notes.length > 0 || collection.itinerary.length > 0) && <div className="research-guides">
        {collection.itinerary.length > 0 && <details className="research-guide"><summary><Icon name="calendars" />A suggested week</summary><div className="research-itinerary">{collection.itinerary.map((day, i) => <section key={i}><h3>{researchDateLabel(day.date)}</h3><ResearchProse text={day.body} /></section>)}</div></details>}
        {collection.notes.length > 0 && <details className="research-guide"><summary><Icon name="list" />How to use this research</summary><div>{collection.notes.map((note, i) => <section key={i}><h3>{note.title}</h3><ResearchProse text={note.body} /></section>)}</div></details>}
      </div>}
      <section className="research-browser" aria-label="Explore Mumbai research">
        <div className="research-view-tabs" role="group" aria-label="Research selection">{RESEARCH_VIEWS.map((view) => <button key={view.id} type="button" className={`chip${filters.view === view.id ? " on" : ""}`} aria-pressed={filters.view === view.id} onClick={() => set({ view: view.id, priority: "" })}>{view.label}<span className="chip-count">{countFor(view.id)}</span></button>)}</div>
        <form className="research-controls" role="search" onSubmit={(e) => e.preventDefault()}>
          <label className="search research-search"><Icon name="search" /><span className="sr-only">Search all research notes, speakers, and venues</span><input type="search" value={filters.q} onChange={(e) => set({ q: e.target.value })} placeholder="Search people, ideas, venues, notes…" /></label>
          <div className="research-selects">
            <label>Date<select value={filters.day} onChange={(e) => set({ day: e.target.value })}><option value="">Any date</option>{days.map((day) => <option key={day} value={day}>{researchDateLabel(`${day}T12:00:00+05:30`)}</option>)}<option value="undated">Undated / places</option></select></label>
            <label>Type<select value={filters.kind} onChange={(e) => set({ kind: e.target.value })}><option value="">All types</option>{Object.entries(KIND_LABELS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
            <label>Priority<select value={filters.priority} onChange={(e) => set({ priority: e.target.value, view: "all" })}><option value="">Any priority</option>{Object.entries(PRIORITY_LABELS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
          </div>
        </form>
        <div className="research-results-row"><p role="status">{shown.length} {shown.length === 1 ? "result" : "results"}{filters.view === "curated" ? " · strongest fits first" : ""}</p>{(filters.q || filters.day || filters.kind || filters.priority) && <button type="button" className="button subtle small" onClick={() => set({ q: "", day: "", kind: "", priority: "" })}>Clear filters</button>}</div>
        {shown.length > 0 ? <div className="research-grid">{shown.map((entry) => <ResearchCard key={entry.id} entry={entry} />)}</div> : <EmptyState title="No Mumbai picks match"><p>Try another date or search, or look across the full collection.</p><button type="button" className="button" onClick={() => set({ view: "all", q: "", day: "", kind: "", priority: "" })}>Show all research</button></EmptyState>}
      </section>
    </>}
  </Page>;
}

export function ResearchPage({ id }: { id: string }) {
  const { catalog, loading } = useData();
  const entry = mumbaiEntries(catalog?.researchCollections ?? []).find((e) => e.id === id);
  useEffect(() => { document.title = `${entry?.title ?? "Mumbai research"} · Events`; }, [entry]);
  return <div className="page event-page research-page"><div className="page-header back-row"><Link className="button subtle" to="mumbai"><Icon name="chevronLeft" size={18} />Mumbai research</Link></div>
    {entry ? <ResearchDetail entry={entry} /> : loading ? <LoadingCards count={1} /> : <EmptyState title="Research entry not found"><p>Return to the Mumbai collection to browse the current notes.</p><Link to="mumbai" className="button">Explore Mumbai</Link></EmptyState>}
  </div>;
}
