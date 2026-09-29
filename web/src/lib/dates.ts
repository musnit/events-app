// Calendar arithmetic in the viewer's local time zone. Day keys are "YYYY-MM-DD", month keys "YYYY-MM".

export type DayKey = string;

const DAY_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
const MONTH_RE = /^(\d{4})-(\d{2})$/;
const pad = (n: number) => String(n).padStart(2, "0");

export function dayKey(d: Date): DayKey {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function monthKey(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
}

/** Local midnight for a day key, or null when the key is not a real date. */
export function parseDayKey(key: string): Date | null {
  const m = DAY_RE.exec(key);
  if (!m) return null;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return dayKey(d) === key ? d : null;
}

export function parseMonthKey(key: string): Date | null {
  const m = MONTH_RE.exec(key);
  if (!m) return null;
  const month = Number(m[2]);
  if (month < 1 || month > 12) return null;
  return new Date(Number(m[1]), month - 1, 1);
}

export function startOfDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

/** Adds calendar days (safe across daylight-saving changes). */
export function addDays(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
}

export function addMonths(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth() + n, 1);
}

export function startOfWeek(d: Date, weekStartsOn = 0): Date {
  const day = startOfDay(d);
  return addDays(day, -((day.getDay() - weekStartsOn + 7) % 7));
}

/** The days shown in a month grid: whole weeks covering the month. */
export function monthGrid(month: Date, weekStartsOn = 0): Date[] {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const last = new Date(month.getFullYear(), month.getMonth() + 1, 0);
  const start = startOfWeek(first, weekStartsOn);
  const end = addDays(startOfWeek(last, weekStartsOn), 6);
  const days: Date[] = [];
  for (let d = start; d <= end; d = addDays(d, 1)) days.push(d);
  return days;
}

export function localDayOf(iso: string): DayKey {
  return dayKey(new Date(iso));
}

/** Friday evening through Sunday counts as the weekend. */
export function isWeekend(iso: string): boolean {
  const d = new Date(iso);
  const day = d.getDay();
  return day === 0 || day === 6 || (day === 5 && d.getHours() >= 17);
}

/** The next weekend's Friday, or today when today is already part of the weekend. */
export function weekendStart(today: Date): Date {
  const day = today.getDay();
  if (day === 5 || day === 6 || day === 0) return startOfDay(today);
  return addDays(startOfDay(today), 5 - day);
}

export type TimeOfDay = "morning" | "afternoon" | "evening" | "late";

export function timeOfDay(iso: string): TimeOfDay {
  const h = new Date(iso).getHours();
  if (h >= 5 && h < 12) return "morning";
  if (h >= 12 && h < 17) return "afternoon";
  if (h >= 17 && h < 21) return "evening";
  return "late";
}

export function daysBetween(a: Date, b: Date): number {
  return Math.round((startOfDay(b).getTime() - startOfDay(a).getTime()) / 86_400_000);
}
