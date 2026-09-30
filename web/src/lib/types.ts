// Shapes returned by the Python API (see events/catalog.py and events/web.py).

export type Source = "luma" | "partiful" | "agihouse";
export type Area = "bay" | "online" | "elsewhere" | "unknown";

export interface Location {
  type: "offline" | "online" | "unknown";
  venue: string | null;
  address: string | null;
  city: string | null;
  neighborhood: string | null;
  region: string | null;
  country: string | null;
  lat: number | null;
  lng: number | null;
}

export interface Person {
  name: string;
  avatar_url: string | null;
}

export interface Presenter {
  id: string | null;
  name: string | null;
  avatar_url: string | null;
  url: string | null;
  description: string | null;
}

export interface Ticket {
  free: boolean | null;
  price_cents: number | null;
  max_price_cents: number | null;
  currency: string | null;
  sold_out: boolean;
  spots_left: number | null;
  approval: boolean;
  waitlist: boolean;
  availability: string | null;
}

export interface EventItem {
  id: string;
  source: Source;
  name: string;
  url: string;
  start_at: string;
  end_at: string | null;
  all_day: boolean;
  timezone: string | null;
  cover_url: string | null;
  location: Location;
  presenter: Presenter | null;
  hosts: Person[];
  tags: string[];
  ticket: Ticket | null;
  guest_count: number | null;
  going: boolean;
  going_status: string | null;
  calendar_ids: string[];
  first_seen_at: string | null;
  announced_at: string | null;
  also_on: { source: Source; url: string }[];
  area: Area;
  zone: string | null;
  vibes: string[];
  topics: string[];
  size: string | null;
  starred: boolean;
  hidden: boolean;
  muted: boolean;
}

export interface CalendarItem {
  id: string;
  source: Source;
  name: string;
  avatar_url: string | null;
  url: string | null;
  tint_color: string | null;
  description: string | null;
  origins: string[];
  muted: boolean;
  upcoming_count: number;
  last_ok_at: string | null;
  last_error: string | null;
}

export interface Category {
  id: string;
  label: string;
  emoji: string;
  hint: string;
}

export interface Taxonomy {
  version: number;
  vibes: Category[];
  topics: Category[];
  sizes: { id: string; label: string; hint: string }[];
}

export interface EventsPayload {
  version: string;
  generated_at: string;
  events: EventItem[];
  calendars: CalendarItem[];
  taxonomy: Taxonomy;
  zones: { id: string; label: string }[];
}

export interface WorkerStatus {
  name: string;
  busy: boolean;
  current: string | null;
  done: number;
  failed: number;
  total: number;
  queued_by_user: number;
  started_at: number | null;
  last_finished_at: number | null;
  paused_until: number | null;
  last_error: string | null;
}

export interface FeedStatus {
  key: string;
  source: Source;
  kind: string;
  calendar_id: string | null;
  label: string;
  last_attempt_at: number | null;
  last_ok_at: number | null;
  last_error: string | null;
  failures: number;
  retry_at: number | null;
  item_count: number | null;
}

export interface Status {
  version: string;
  data_version: number;
  luma: {
    session: boolean;
    session_via: string | null;
    session_notice: string | null;
    ics: string | null;
    calendars: number;
    calendars_by_origin: Record<string, number>;
    going_snapshot: number;
  };
  partiful: {
    accounts: { uid: string; name: string | null; added_at: number }[];
    feed: string | null;
  };
  workers: Record<string, WorkerStatus>;
  feeds: FeedStatus[];
  prefs: { muted_calendars?: string[]; area?: AreaChoice };
}

export type AreaChoice = "bay" | "bay-online" | "all";
