// "New since your last visit": the reference moment only moves forward when a new visit starts,
// so badges stay put while you browse and clear the next time you come back.

const KEY = "lumacal.visit";
const NEW_VISIT_AFTER_MS = 30 * 60_000;

interface VisitState {
  lastActive: number;
  reference: number;
}

function read(): VisitState | null {
  try {
    const data = JSON.parse(localStorage.getItem(KEY) || "null");
    return data && typeof data.lastActive === "number" && typeof data.reference === "number" ? data : null;
  } catch {
    return null;
  }
}

function write(state: VisitState): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    /* private mode: badges just won't persist */
  }
}

/** Call once per page load. Returns the moment after which announced events count as new. */
export function startVisit(now = Date.now()): number {
  const saved = read();
  // The first ever visit has nothing to compare against, so nothing is new yet.
  const reference = !saved ? now : now - saved.lastActive > NEW_VISIT_AFTER_MS ? saved.lastActive : saved.reference;
  write({ lastActive: now, reference });
  return reference;
}

export function touchVisit(now = Date.now()): void {
  const saved = read();
  if (saved) write({ ...saved, lastActive: now });
}
