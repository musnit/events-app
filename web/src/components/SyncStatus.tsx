import { timeAgo } from "../lib/format.ts";
import type { Status, WorkerStatus } from "../lib/types.ts";
import { useData } from "../state/data.ts";
import { useNow } from "../state/hooks.ts";

const NAMES: Record<string, string> = { luma: "Luma", partiful: "Partiful", agihouse: "AGI House" };

/** When each source last pulled something successfully (kept across restarts, unlike worker runs). */
export function lastSynced(status: Status, source?: string): number {
  return Math.max(0, ...status.feeds.filter((f) => !source || f.source === source).map((f) => f.last_ok_at ?? 0));
}

export function workerLine(w: WorkerStatus, now: number, lastOk = 0): string {
  if (w.paused_until && w.paused_until * 1000 > now) return `paused for ${Math.ceil(w.paused_until - now / 1000)}s (rate limited)`;
  if (w.busy) return w.total > 1 ? `syncing ${Math.min(w.done + w.failed + 1, w.total)} of ${w.total}` : "syncing";
  const last = Math.max(w.last_finished_at ?? 0, lastOk);
  return last ? `synced ${timeAgo(last, now)}` : "not synced yet";
}

/** One compact line about background sync, for the sidebar and the top of the Sources page. */
export function SyncSummary() {
  const { status } = useData();
  const now = useNow(5_000);
  if (!status) return null;
  const workers = Object.values(status.workers);
  const busy = workers.filter((w) => w.busy);
  if (!busy.length) {
    const last = Math.max(lastSynced(status), ...workers.map((w) => w.last_finished_at ?? 0));
    return <p className="sync-summary">{last ? `Up to date · synced ${timeAgo(last, now)}` : "Waiting for the first sync"}</p>;
  }
  return (
    <div className="sync-summary busy" role="status">
      {busy.map((w) => {
        const pct = w.total > 1 ? Math.round(((w.done + w.failed) / w.total) * 100) : null;
        return (
          <div key={w.name} className="sync-row">
            <span>{NAMES[w.name] ?? w.name}: {workerLine(w, now, lastSynced(status, w.name))}</span>
            {pct !== null && <progress max={100} value={pct} aria-label={`${NAMES[w.name]} sync progress`} />}
          </div>
        );
      })}
    </div>
  );
}
