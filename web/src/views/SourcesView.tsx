import { useEffect, useId, useState, useSyncExternalStore, type FormEvent, type ReactNode } from "react";
import { Icon } from "../components/Icon.tsx";
import { Link } from "../components/Link.tsx";
import { Page } from "../components/Page.tsx";
import { lastSynced, SyncSummary, workerLine } from "../components/SyncStatus.tsx";
import { api, apiUrl } from "../lib/api.ts";
import { appRootUrl, lumaBookmarklet, partifulBookmarklet } from "../lib/bookmarklets.ts";
import { pluralize, timeAgo } from "../lib/format.ts";
import type { PwaController } from "../lib/pwa.ts";
import { href, type SourcesSection } from "../lib/routes.ts";
import { data, useData } from "../state/data.ts";
import { useNow } from "../state/hooks.ts";
import { toast } from "../state/toasts.ts";

type Platform = "ios" | "android" | "desktop";

function detectPlatform(): Platform {
  const ua = navigator.userAgent;
  if (/iPhone|iPad|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)) return "ios";
  return /Android/.test(ua) ? "android" : "desktop";
}

function Section({ id, title, children, intro }: { id: SourcesSection; title: string; intro?: ReactNode; children: ReactNode }) {
  return (
    <section id={`section-${id}`} className="panel" aria-labelledby={`section-${id}-title`}>
      <h2 id={`section-${id}-title`}>{title}</h2>
      {intro && <div className="panel-intro">{intro}</div>}
      {children}
    </section>
  );
}

/** A small form that posts one value, shows progress and the result. */
function ValueForm({ label, placeholder, submit, onDone, multiline, type = "text", button = "Save" }: {
  label: string;
  placeholder: string;
  submit: (value: string) => Promise<string>;
  onDone?: () => void;
  multiline?: boolean;
  type?: string;
  button?: string;
}) {
  const id = useId();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!value.trim()) return;
    setBusy(true);
    setMessage(null);
    try {
      const text = await submit(value.trim());
      setValue("");
      setMessage({ text, error: false });
      onDone?.();
    } catch (e) {
      setMessage({ text: (e as Error).message, error: true });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="value-form" onSubmit={(e) => void onSubmit(e)}>
      <label htmlFor={id}>{label}</label>
      <div className="value-form-row">
        {multiline
          ? <textarea id={id} rows={3} value={value} onChange={(e) => setValue(e.target.value)} placeholder={placeholder} />
          : <input id={id} type={type} value={value} onChange={(e) => setValue(e.target.value)} placeholder={placeholder}
              autoComplete="off" autoCapitalize="off" spellCheck={false} />}
        <button type="submit" className="button primary" disabled={busy || !value.trim()}>{busy ? "Working…" : button}</button>
      </div>
      {message && <p className={message.error ? "error-text" : "success-text"} role="status">{message.text}</p>}
    </form>
  );
}

function BookmarkletSteps({ code, site, siteUrl, name }: { code: string; site: string; siteUrl: string; name: string }) {
  const [platform, setPlatform] = useState<Platform>(detectPlatform);
  const [copied, setCopied] = useState(false);
  const [showText, setShowText] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2500);
    } catch {
      setShowText(true);
    }
  }

  const open = <a href={siteUrl} target="_blank" rel="noopener noreferrer">{site}</a>;
  return (
    <div className="bookmarklet">
      <div className="segmented small" role="tablist" aria-label="Your device">
        {(["ios", "android", "desktop"] as Platform[]).map((p) => (
          <button key={p} type="button" role="tab" aria-selected={platform === p} className={platform === p ? "on" : ""} onClick={() => setPlatform(p)}>
            {p === "ios" ? "iPhone / iPad" : p === "android" ? "Android" : "Computer"}
          </button>
        ))}
      </div>
      <ol className="steps">
        {platform === "ios" && <>
          <li>Tap <b>Copy bookmarklet</b> below.</li>
          <li>In Safari tap <b>Share → Add Bookmark</b> and save it (it bookmarks this page; that's fine).</li>
          <li>Open <b>Bookmarks → Edit</b>, tap the new bookmark, replace its address with what you copied and name it “{name}”.</li>
          <li>Go to {open} signed in, open Bookmarks and tap “{name}”. You'll land back here with everything imported.</li>
        </>}
        {platform === "android" && <>
          <li>Tap <b>Copy bookmarklet</b> below.</li>
          <li>In Chrome tap <b>⋮ → ☆</b> to bookmark this page, then <b>⋮ → Bookmarks</b>, edit the bookmark, paste what you copied as its URL and name it “{name}”.</li>
          <li>Open {open} signed in, type “{name}” in the address bar and pick the bookmark.</li>
        </>}
        {platform === "desktop" && <>
          <li>Drag <b>{name}</b> below onto your bookmarks bar (or copy it and make a bookmark with it as the address).</li>
          <li>Open {open} signed in and click the bookmark.</li>
        </>}
      </ol>
      <div className="row-wrap">
        <button type="button" className="button primary" onClick={() => void copy()}>
          {copied ? <><Icon name="check" size={16} /> Copied</> : "Copy bookmarklet"}
        </button>
        {platform === "desktop" && (
          // Only for dragging to the bookmarks bar; a click copies instead of running it here.
          <a className="bookmarklet-link" href={code} onClick={(e) => { e.preventDefault(); void copy(); }} title="Drag me to your bookmarks bar">{name}</a>
        )}
      </div>
      {showText && <textarea className="code" readOnly rows={3} value={code} onFocus={(e) => e.currentTarget.select()} aria-label="Bookmarklet code" />}
    </div>
  );
}

function InstallApp({ pwa }: { pwa: PwaController }) {
  const state = useSyncExternalStore(pwa.subscribe, pwa.getSnapshot);
  if (state.standalone) return <p className="muted">Running as an installed app.</p>;
  return (
    <div>
      {state.canPrompt || state.installing ? (
        <button type="button" className="button primary" disabled={state.installing} onClick={() => void pwa.install()}>
          {state.installing ? "Opening install dialog…" : "Install app"}
        </button>
      ) : state.showPromotion ? (
        <p>To put Events on your home screen: in Safari use <b>Share → Add to Home Screen</b>; in Chrome use <b>⋮ → Install app</b> or <b>Add to Home screen</b>.</p>
      ) : (
        <p className="muted">Installation offered already. You can install any time from your browser's menu.</p>
      )}
      {state.installError && <p className="error-text">Installation could not be started. You can still use this page.</p>}
      {state.workerStatus === "failed" && <p className="muted small">Offline support could not start; the app still works while connected.</p>}
    </div>
  );
}

export function SourcesView({ section, pwa }: { section: SourcesSection | null; pwa: PwaController }) {
  const { status, catalog } = useData();
  const now = useNow(5_000);
  const [syncing, setSyncing] = useState(false);
  const root = appRootUrl();

  useEffect(() => {
    if (!section) return;
    const node = document.getElementById(`section-${section}`);
    node?.scrollIntoView({ block: "start" });
  }, [section]);

  const changed = () => void data.afterSourcesChanged();

  async function syncNow(source?: string) {
    setSyncing(true);
    try {
      const { queued } = await api.sync(source);
      toast(`Syncing ${pluralize(queued, "source")}`);
      changed();
    } catch (e) {
      toast(`Could not start a sync: ${(e as Error).message}`, "error");
    } finally {
      setSyncing(false);
    }
  }

  async function run(action: () => Promise<unknown>, done: string) {
    try {
      await action();
      toast(done);
      changed();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }

  const failing = (status?.feeds ?? []).filter((f) => f.last_error);
  const luma = status?.luma;
  const linkCalendars = (catalog?.calendars ?? []).filter((c) => c.origins.includes("link"));
  const linkedEvents = luma?.linked_events ?? [];

  return (
    <Page title="Sources" eyebrow="Where your events come from">
      <nav className="section-nav" aria-label="Sections">
        {([["sync", "Sync"], ["luma", "Luma"], ["partiful", "Partiful"], ["export", "Export"], ["app", "App"]] as [SourcesSection, string][]).map(([id, label]) => (
          <Link key={id} to={href({ name: "sources", section: id })} options={{ replace: true, keepScroll: true }}
            className={`chip${section === id ? " on" : ""}`}>{label}</Link>
        ))}
      </nav>

      <Section id="sync" title="Sync">
        <SyncSummary />
        {status && (
          <ul className="worker-list">
            {Object.values(status.workers).map((w) => (
              <li key={w.name}>
                <div>
                  <span className="strong">{w.name === "agihouse" ? "AGI House" : w.name === "luma" ? "Luma" : "Partiful"}</span>
                  <span className="muted"> · {workerLine(w, now, lastSynced(status, w.name))}</span>
                  {w.current && <div className="muted small truncate">Now: {w.current}</div>}
                </div>
                <button type="button" className="button small" disabled={syncing} onClick={() => void syncNow(w.name)}>
                  <Icon name="refresh" size={16} /> Sync
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="row-wrap">
          <button type="button" className="button primary" disabled={syncing} onClick={() => void syncNow()}>
            <Icon name="refresh" size={18} /> Sync everything now
          </button>
        </div>
        <p className="muted small">
          Luma calendars refresh every 4 hours, 15 seconds apart, because Luma blocks bursts. Your own RSVPs and Partiful refresh hourly, AGI House every 2 hours.
          Anything you add syncs right away, and events appear as each calendar finishes.
        </p>
        {failing.length > 0 && (
          <details className="failures">
            <summary>{pluralize(failing.length, "source")} failed last time</summary>
            <ul>
              {failing.map((f) => (
                <li key={f.key}>
                  <span className="strong">{f.label}</span>: {f.last_error}
                  <span className="muted small"> · retry {f.retry_at ? `after ${new Date(f.retry_at * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}` : "soon"}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
      </Section>

      <Section id="luma" title="Luma" intro={
        <p>Luma has no way for another app to ask who you follow, so a one-time bookmark does it from your own signed-in browser. Run it again whenever you follow new calendars.</p>
      }>
        {luma && (
          <p className="status-line">
            <Icon name={luma.calendars ? "check" : "alert"} size={16} />
            {luma.calendars ? `${pluralize(luma.calendars, "calendar")} connected` : "No Luma calendars yet"}
            {luma.calendars_by_origin.config ? ` · ${luma.calendars_by_origin.config} set by the app's configuration` : ""}
            {luma.going_snapshot ? ` · ${pluralize(luma.going_snapshot, "RSVP")} from your last import` : ""}
            {luma.linked_events.length ? ` · ${pluralize(luma.linked_events.length, "event")} added by link` : ""}
          </p>
        )}
        <h3>Import the calendars you follow</h3>
        <BookmarkletSteps code={lumaBookmarklet(root)} site="luma.com/home" siteUrl="https://luma.com/home" name="Import Luma" />

        <details className="more" open={linkCalendars.length + linkedEvents.length > 0}>
          <summary>Add calendars or events by link</summary>
          <p className="muted">
            In the Luma app, open a calendar or an event → Share → Copy link, and paste one or more links. A calendar link follows the
            calendar. An event link adds just that event, private ones included, and it shows whichever area you choose.
          </p>
          <ValueForm label="Calendar or event links" placeholder="https://luma.com/…" multiline button="Add"
            submit={async (text) => {
              const r = await api.lumaAddLinks(text);
              const added = r.added.length ? `Added ${r.added.map((a) => a.name).join(", ")}.` : "Nothing new added.";
              return r.failed.length ? `${added} Skipped: ${r.failed.map((f) => `${f.link} (${f.error})`).join("; ")}` : added;
            }} onDone={changed} />
          {linkCalendars.length > 0 && (
            <ul className="chip-list" aria-label="Calendars added by link">
              {linkCalendars.map((c) => (
                <li key={c.id} className="chip static">
                  {c.name}
                  <button type="button" className="icon-button small" aria-label={`Remove ${c.name}`}
                    onClick={() => void run(() => api.lumaRemoveCalendar(c.id), `Removed ${c.name}`)}>
                    <Icon name="close" size={14} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          {linkedEvents.length > 0 && (
            <ul className="chip-list" aria-label="Events added by link">
              {linkedEvents.map((e) => {
                const name = e.name ?? e.id;
                return (
                  <li key={e.id} className="chip static" title={e.last_error ? `Could not refresh: ${e.last_error}` : undefined}>
                    {e.last_error && <Icon name="alert" size={14} />}
                    <Link to={href({ name: "event", id: e.id })}>{name}</Link>
                    <button type="button" className="icon-button small" aria-label={`Remove ${name}`}
                      onClick={() => void run(() => api.lumaRemoveEvent(e.id), `Removed ${name}`)}>
                      <Icon name="close" size={14} />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </details>

        <details className="more" open={!!luma?.ics}>
          <summary>Keep your Luma RSVPs up to date {luma?.ics ? "(connected)" : "(recommended)"}</summary>
          <p className="muted">The bookmarklet sees your RSVPs only when you run it. Your personal iCal link keeps them current: in Luma open <b>Settings → Calendar Syncing → Add iCal Subscription</b>, choose <b>Google Calendar</b> and copy the link it opens.</p>
          {luma?.ics ? (
            <div className="row-wrap">
              <span className="status-line"><Icon name="check" size={16} /> {luma.ics}</span>
              <button type="button" className="button small subtle" onClick={() => void run(api.lumaClearIcs, "Removed the Luma iCal link")}>Remove</button>
            </div>
          ) : (
            <ValueForm label="Personal iCal link" placeholder="https://api.lu.ma/ics/get?entity=user&id=… or the Google link"
              submit={async (url) => `Saved. ${pluralize((await api.lumaSetIcs(url)).events, "upcoming RSVP")} found.`} onDone={changed} />
          )}
        </details>

        <details className="more" open={!!luma?.session || !!luma?.session_notice}>
          <summary>Sync your followed list automatically {luma?.session ? "(connected)" : "(computer only)"}</summary>
          {luma?.session_notice && <p className="error-text">{luma.session_notice}</p>}
          {luma?.session ? (
            <div className="row-wrap">
              <span className="status-line"><Icon name="check" size={16} /> Luma session saved; follows and RSVPs sync on their own.</span>
              <button type="button" className="button small subtle" onClick={() => void run(api.lumaClearSession, "Forgot the Luma session")}>Forget</button>
            </div>
          ) : (
            <>
              <p className="muted">Sign in at luma.com, open DevTools → Application → Cookies → luma.com and copy the value of <code>luma.auth-session-key</code>.</p>
              <ValueForm label="Session cookie" placeholder="luma.auth-session-key value" type="password"
                submit={async (key) => { await api.lumaSetSession(key); return "Session saved. Syncing your follows…"; }} onDone={changed} />
            </>
          )}
        </details>
      </Section>

      <Section id="partiful" title="Partiful" intro={
        <p>A one-time bookmark on partiful.com hands this app your Partiful login. It then reads events from people and groups you follow plus your own invites and RSVPs, and keeps them fresh.</p>
      }>
        {status && status.partiful.accounts.length > 0 && (
          <ul className="account-list">
            {status.partiful.accounts.map((a) => (
              <li key={a.uid}>
                <Icon name="check" size={16} />
                <span className="strong">{a.name || "Partiful account"}</span>
                <span className="muted small">connected {timeAgo(a.added_at, now)}</span>
                <button type="button" className="button small subtle" onClick={() => {
                  if (window.confirm(`Disconnect ${a.name || "this Partiful account"}?`)) void run(() => api.partifulRemove(a.uid), "Disconnected");
                }}>Disconnect</button>
              </li>
            ))}
          </ul>
        )}
        <h3>{status?.partiful.accounts.length ? "Connect another account" : "Connect Partiful"}</h3>
        <BookmarkletSteps code={partifulBookmarklet(root)} site="partiful.com/events" siteUrl="https://partiful.com/events" name="Import Partiful" />
        <details className="more" open={!!status?.partiful.feed}>
          <summary>Or just your Partiful calendar link {status?.partiful.feed ? "(connected)" : ""}</summary>
          <p className="muted">Covers only events you're invited to or going to. On any Partiful event tap the calendar icon, choose <b>Google Calendar</b>, then <b>Copy Link</b>.</p>
          {status?.partiful.feed ? (
            <div className="row-wrap">
              <span className="status-line"><Icon name="check" size={16} /> {status.partiful.feed}</span>
              <button type="button" className="button small subtle" onClick={() => void run(api.partifulClearFeed, "Removed the Partiful link")}>Remove</button>
            </div>
          ) : (
            <ValueForm label="Partiful calendar link" placeholder="webcal://calendars.partiful.com/getCalendar?id=…"
              submit={async (url) => `Saved. ${pluralize((await api.partifulSetFeed(url)).events, "event")} found.`} onDone={changed} />
          )}
        </details>
        <p className="muted small">AGI House events are included automatically from its public calendar.</p>
      </Section>

      <Section id="export" title="Export">
        <div className="row-wrap">
          <a className="button" href={apiUrl("feed.ics")} download="my-events.ics"><Icon name="download" size={18} /> My plan (.ics)</a>
          <a className="button" href={apiUrl("feed.ics?scope=all")} download="all-events.ics"><Icon name="download" size={18} /> Everything (.ics)</a>
        </div>
        <p className="muted small">
          “My plan” holds events you're going to or starred. Calendar apps can import these files, but can't subscribe to them,
          because this app sits behind the portal sign-in. To add one event to your calendar, use <b>Add to Google Calendar</b> on its page.
        </p>
      </Section>

      <Section id="app" title="App">
        <InstallApp pwa={pwa} />
        <p className="muted small">Version {status?.version ?? "…"} · {catalog ? `${pluralize(catalog.events.length, "event")} loaded` : "loading"}</p>
      </Section>
    </Page>
  );
}
