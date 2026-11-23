import { apiUrl } from "../lib/api.ts";
import { googleCalendarUrl } from "../lib/format.ts";
import { canExportResearch, KIND_LABELS, mumbaiEntries, PRIORITY_LABELS, researchMapsUrl, researchWhen, safeResearchUrl } from "../lib/research.ts";
import { href } from "../lib/routes.ts";
import type { EventItem, ResearchEntry } from "../lib/types.ts";
import { data, useData } from "../state/data.ts";
import { toast } from "../state/toasts.ts";
import { Icon } from "./Icon.tsx";
import { Link } from "./Link.tsx";

export function ResearchProse({ text }: { text: string }) {
  return <div className="research-prose">{text.split(/\n\s*\n/).filter(Boolean).map((p, i) => <p key={i}>{p}</p>)}</div>;
}

export function ResearchDetail({ entry, event }: { entry: ResearchEntry; event?: EventItem }) {
  const { catalog } = useData();
  const entries = mumbaiEntries(catalog?.researchCollections ?? []);
  const ev = event ?? catalog?.events.find((e) => e.research?.id === entry.id || e.research_entries?.some((r) => r.id === entry.id));
  const link = safeResearchUrl(entry.url);
  const maps = researchMapsUrl(entry);
  const calendarEvent = canExportResearch(entry) ? {
    name: entry.title, url: link ?? "", start_at: entry.start_at!, end_at: entry.end_at!, all_day: false,
    location: { type: "offline" as const, venue: entry.venue, address: entry.address, city: entry.city },
  } : null;
  const scheduleDiffers = ev && ev.source !== "research" && entry.schedule_status === "confirmed" &&
    (Date.parse(ev.start_at) !== Date.parse(entry.start_at ?? "") || Date.parse(ev.end_at ?? "") !== Date.parse(entry.end_at ?? ""));

  async function share() {
    const url = new URL(href({ name: "research", id: entry.id }), document.baseURI).toString();
    try {
      if (navigator.share) await navigator.share({ title: entry.title, url });
      else { await navigator.clipboard.writeText(url); toast("Research link copied"); }
    } catch (e) { if ((e as Error).name !== "AbortError") toast("Could not share the link", "error"); }
  }

  return <article className="research-detail event-detail">
    <div className="research-card-labels">
      <span className={`research-priority ${entry.priority}`}>{PRIORITY_LABELS[entry.priority]}</span>
      <span className="eyebrow">{KIND_LABELS[entry.kind]}</span>
    </div>
    <h1 className="detail-title">{entry.title}</h1>
    <p className="research-verdict">{entry.verdict}</p>
    <div className="detail-actions">
      {link && <a className="button primary" href={link} target="_blank" rel="noopener noreferrer">Event / venue page <Icon name="external" size={16} /></a>}
      {ev && <button type="button" className={`button${ev.starred ? " on" : ""}`} aria-pressed={ev.starred} onClick={() => void data.mark(ev.id, { starred: !ev.starred })}>
        <Icon name="star" filled={ev.starred} size={18} />{ev.starred ? "Saved" : "Save"}
      </button>}
      <button type="button" className="button" onClick={() => void share()}><Icon name="share" size={18} />Share research</button>
    </div>
    <dl className="research-facts">
      <div><dt>When · Mumbai time</dt><dd>{researchWhen(entry)}
        {entry.schedule_status !== "confirmed" && <span className="research-status">{entry.schedule_status === "stale" ? "Past / stale listing — not a current recommendation" : entry.schedule_status === "unscheduled" ? "No event date announced" : "Schedule needs confirmation"}</span>}
        {calendarEvent && <div className="fact-links"><a href={googleCalendarUrl(calendarEvent)} target="_blank" rel="noopener noreferrer">Add to Google Calendar</a><a href={apiUrl(`api/research/${encodeURIComponent(entry.id)}/ics`)} download>Download .ics</a></div>}
        {scheduleDiffers && <p className="callout">The synced event listing has different times: {researchWhen({ ...entry, start_at: ev.start_at, end_at: ev.end_at })}. These notes and calendar links use the researched session times above. Check the event page before attending.</p>}
      </dd></div>
      <div><dt>Where</dt><dd>{entry.venue || "Venue to be confirmed"}{entry.address && <div>{entry.address}</div>}<div className="muted">{entry.city}</div>{maps && <a href={maps} target="_blank" rel="noopener noreferrer">Open in Maps</a>}</dd></div>
      {entry.access && <div><dt>Access</dt><dd><ResearchProse text={entry.access} /></dd></div>}
    </dl>
    <section className="detail-section"><h2>Why it makes the list</h2><ResearchProse text={entry.summary} /></section>
    {entry.people.length > 0 && <section className="detail-section"><h2>People & the likely room</h2><div className="research-people">
      {entry.people.map((person, i) => <div key={`${person.name}-${i}`}><h3>{person.name}</h3>{person.role && <p className="muted">{person.role}</p>}<ResearchProse text={person.assessment} /></div>)}
    </div></section>}
    {entry.sections.map((section, i) => <section className="detail-section" key={i}><h2>{section.title}</h2><ResearchProse text={section.body} /></section>)}
    {entry.conflicts.length > 0 && <section className="detail-section research-conflicts"><h2>Clashes & things to check</h2><ul>{entry.conflicts.map((text, i) => {
      const related = entries.find((candidate) => candidate.id === text);
      return <li key={i}>{related ? <Link to={href({ name: "research", id: related.id })}>{related.title}</Link> : text}</li>;
    })}</ul></section>}
    {entry.sources.length > 0 && <section className="detail-section"><h2>Sources to check</h2><ul className="research-sources">{entry.sources.map((source, i) => <li key={i}>
      {safeResearchUrl(source.url) ? <a href={safeResearchUrl(source.url)} target="_blank" rel="noopener noreferrer">{source.label}<Icon name="external" size={14} /></a> : source.label}
    </li>)}</ul></section>}
    {entry.tags.length > 0 && <div className="chips research-tags" aria-label="Topics">{entry.tags.map((tag) => <Link key={tag} to={`mumbai?view=all&q=${encodeURIComponent(tag)}`} className="chip">{tag}</Link>)}</div>}
  </article>;
}
