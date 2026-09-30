// Renders every screen at phone, tablet and desktop sizes in headless Chromium, saves screenshots and
// reports console errors, horizontal overflow, broken images and undersized touch targets.
//
//   BROWSER_BIN=/path/to/chromium BASE_URL=http://127.0.0.1:8771/ npm run check:ui
//
// Optional: OUT_DIR (default /tmp/events-ui), ONLY=phone|desktop|tablet, PAGES=comma list of page names.

import { mkdir } from "node:fs/promises";
import { chromium } from "playwright-core";

const BASE = process.env.BASE_URL ?? "http://127.0.0.1:8771/";
const OUT = process.env.OUT_DIR ?? "/tmp/events-ui";
const BIN = process.env.BROWSER_BIN;
if (!BIN) {
  console.error("Set BROWSER_BIN to a Chromium executable.");
  process.exit(2);
}

const VIEWPORTS = {
  phone: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
  small: { viewport: { width: 360, height: 640 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
  tablet: { viewport: { width: 820, height: 1180 }, isMobile: true, hasTouch: true, deviceScaleFactor: 1 },
  desktop: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 },
};

const pad = (n) => String(n).padStart(2, "0");
const now = new Date();
const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
const month = today.slice(0, 7);

async function firstIds() {
  const res = await fetch(new URL("api/events", BASE));
  const data = await res.json();
  const upcoming = data.events.find((e) => Date.parse(e.start_at) > Date.now() && e.area === "bay" && e.cover_url) ?? data.events[0];
  const calendar = [...data.calendars].sort((a, b) => b.upcoming_count - a.upcoming_count)[0];
  return { event: upcoming?.id, calendar: calendar?.id };
}

async function audit(page) {
  return page.evaluate(() => {
    const doc = document.documentElement;
    const overflow = doc.scrollWidth - window.innerWidth;
    const small = [];
    for (const el of document.querySelectorAll("a, button, input, select, [role=radio], [role=tab], summary")) {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height || r.bottom < 0 || r.top > window.innerHeight) continue;
      const style = getComputedStyle(el);
      if (style.visibility === "hidden" || el.closest(".stretched, .sr-only, .skip-link")) continue;
      if (el.classList.contains("stretched") || getComputedStyle(el, "::after").position === "absolute") continue;
      // Links inside running text are exempt from target-size rules (WCAG 2.5.8 "inline").
      const parentText = (el.parentElement?.textContent || "").trim().length;
      if (el.tagName === "A" && parentText > (el.textContent || "").trim().length + 12) continue;
      if (r.height < 24 || r.width < 24) small.push(`${el.tagName.toLowerCase()}.${[...el.classList].join(".")} "${(el.textContent || el.getAttribute("aria-label") || "").trim().slice(0, 30)}" ${Math.round(r.width)}×${Math.round(r.height)}`);
    }
    const wide = [...document.querySelectorAll("body *")].filter((el) => {
      const r = el.getBoundingClientRect();
      return r.right > window.innerWidth + 1 && getComputedStyle(el).position !== "fixed" && !el.closest(".vibe-strip, .jump-row");
    }).slice(0, 5).map((el) => `${el.tagName.toLowerCase()}.${[...el.classList].join(".")}`);
    const brokenImages = [...document.images].filter((img) => img.complete && img.naturalWidth === 0 && img.getBoundingClientRect().width > 0).length;
    return { overflow, small: small.slice(0, 12), wide, brokenImages, title: document.title };
  });
}

const browser = await chromium.launch({ executablePath: BIN, args: ["--no-sandbox", "--disable-gpu"] });
await mkdir(OUT, { recursive: true });
const ids = await firstIds();
const PAGES = [
  ["upcoming", ""],
  ["upcoming-party", "?vibe=party"],
  ["upcoming-filtered", "?vibe=learn,social&topic=ai&time=evening"],
  ["search", "?q=hack"],
  ["day", `day/${today}`],
  ["month", `month/${month}`],
  ["saved", "saved"],
  ["calendars", "calendars"],
  ["calendar", `calendars/${ids.calendar}`],
  ["sources", "sources"],
  ["sources-luma", "sources/luma"],
  ["event", `event/${ids.event}`],
  ["not-found", "nope/at/all"],
];
const only = process.env.ONLY?.split(",");
const pageFilter = process.env.PAGES?.split(",");
let problems = 0;

for (const [vpName, options] of Object.entries(VIEWPORTS)) {
  if (only && !only.includes(vpName)) continue;
  for (const scheme of ["light", "dark"]) {
    if (scheme === "dark" && vpName !== "phone" && vpName !== "desktop") continue;
    const context = await browser.newContext({ ...options, colorScheme: scheme, timezoneId: "America/Los_Angeles", locale: "en-US" });
    const page = await context.newPage();
    const errors = [];
    page.on("console", (msg) => msg.type() === "error" && errors.push(msg.text()));
    page.on("pageerror", (err) => errors.push(String(err)));
    for (const [name, path] of PAGES) {
      if (pageFilter && !pageFilter.includes(name)) continue;
      if (scheme === "dark" && !["upcoming", "month", "event", "sources"].includes(name)) continue;
      errors.length = 0;
      await page.goto(new URL(path, BASE).toString(), { waitUntil: "networkidle" });
      await page.waitForTimeout(300);
      const result = await audit(page);
      const file = `${OUT}/${vpName}-${scheme}-${name}.png`;
      await page.screenshot({ path: file });
      const issues = [];
      if (errors.length) issues.push(`console: ${errors.join(" | ").slice(0, 300)}`);
      if (result.overflow > 1) issues.push(`horizontal overflow ${result.overflow}px (${result.wide.join(", ")})`);
      if (result.brokenImages) issues.push(`${result.brokenImages} broken images`);
      if (result.small.length && vpName !== "desktop") issues.push(`small targets: ${result.small.join("; ")}`);
      problems += issues.length;
      console.log(`${vpName}/${scheme}/${name}: ${issues.length ? issues.join("\n    ") : "ok"}  [${result.title}]`);
    }
    if (scheme === "light" && (!pageFilter || pageFilter.includes("interactions"))) {
      // Star and unstar an event: a real browser write through the API's same-origin checks.
      await page.goto(BASE, { waitUntil: "networkidle" });
      const star = page.locator(".event-card .star-button").first();
      const before = await star.getAttribute("aria-pressed");
      const saved = page.waitForResponse((r) => r.url().includes("/mark") && r.request().method() === "PUT");
      await star.click();
      const response = await saved;
      await page.waitForTimeout(200);
      const after = await star.getAttribute("aria-pressed");
      const restored = page.waitForResponse((r) => r.url().includes("/mark") && r.request().method() === "PUT");
      await star.click();
      await restored;
      const ok = response.status() === 200 && before !== after;
      if (!ok) problems++;
      console.log(`${vpName}/${scheme}/star: ${ok ? "ok" : `FAILED (status ${response.status()}, ${before} -> ${after})`}`);

      // Open an event over the list, then close it again; open the filter sheet on small screens.
      await page.goto(BASE, { waitUntil: "networkidle" });
      await page.locator(".event-title a").first().click();
      await page.waitForSelector(".sheet-panel .event-detail");
      await page.waitForTimeout(300);
      await page.screenshot({ path: `${OUT}/${vpName}-${scheme}-event-sheet.png` });
      await page.keyboard.press("Escape");
      await page.waitForSelector(".sheet-panel", { state: "detached" });
      const back = page.url();
      console.log(`${vpName}/${scheme}/event-sheet: ok (closed back to ${new URL(back).pathname})`);
      const filterButton = page.locator(".page-actions button[aria-label^='Filters']");
      if (await filterButton.count()) {
        await filterButton.click();
        await page.waitForSelector(".sheet-panel .filter-panel");
        await page.waitForTimeout(250);
        await page.screenshot({ path: `${OUT}/${vpName}-${scheme}-filter-sheet.png` });
        await page.locator(".sheet-panel .filter-panel .chip").nth(2).click();
        await page.waitForTimeout(200);
        console.log(`${vpName}/${scheme}/filter-sheet: ok (url ${new URL(page.url()).search})`);
        await page.keyboard.press("Escape");
      }
    }
    await context.close();
  }
}
await browser.close();
console.log(problems ? `\n${problems} problem(s); screenshots in ${OUT}` : `\nNo problems; screenshots in ${OUT}`);
process.exitCode = problems ? 1 : 0;
