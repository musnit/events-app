// Luma followed-calendars view. Talks to the local server with relative URLs so it works under /luma/.
const $ = (s) => document.querySelector(s);
const state = { data: null, month: startOfMonth(new Date()), hidden: new Set(), goingOnly: false, view: 'grid', email: '' };
const PALETTE = ['#7c9cff','#ff8a65','#4dd0a1','#ffca4d','#c884ff','#4fc3f7','#f06292','#a5d66f','#ffab40','#80cbc4','#b39ddb','#e57373'];

function startOfMonth(d) { return new Date(d.getFullYear(), d.getMonth(), 1); }
function dayKey(d) { return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; }
function fmtTime(iso) { return new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); }
function fmtDay(d) { return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' }); }

async function api(path, body) {
  const r = await fetch(path, body ? { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) } : {});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

// ---- sign in ----
$('#email-form').onsubmit = async (e) => {
  e.preventDefault();
  state.email = $('#email').value.trim();
  $('#signin-msg').textContent = 'Sending code…';
  try { await api('api/auth/send-code', { email: state.email }); $('#email-form').hidden = true; $('#code-form').hidden = false; $('#code').focus(); $('#signin-msg').textContent = ''; }
  catch (err) { $('#signin-msg').textContent = err.message; }
};
$('#code-form').onsubmit = async (e) => {
  e.preventDefault();
  $('#signin-msg').textContent = 'Signing in…';
  try {
    const r = await api('api/auth/verify', { email: state.email, code: $('#code').value.trim() });
    if (r.two_factor) { state.tfa = r; $('#code-form').hidden = true; $('#tfa-form').hidden = false; $('#tfa').focus(); $('#signin-msg').textContent = ''; return; }
    $('#signin-msg').textContent = 'Signed in. Loading your calendars…'; state.showSources = false; pollUntilData();
  } catch (err) { $('#signin-msg').textContent = err.message; }
};
$('#tfa-form').onsubmit = async (e) => {
  e.preventDefault();
  try { await api('api/auth/two-factor', { email: state.email, code: $('#tfa').value.trim(), ...state.tfa }); $('#signin-msg').textContent = 'Signed in. Loading…'; state.showSources = false; pollUntilData(); }
  catch (err) { $('#signin-msg').textContent = err.message; }
};
$('#signout').onclick = async () => { await api('api/auth/signout', {}); load(); };
$('#sources-btn').onclick = () => { state.showSources = true; render(state.data); };
$('#back-btn').onclick = () => { state.showSources = false; render(state.data); };
$('#partiful-form').onsubmit = async (e) => {
  e.preventDefault();
  $('#partiful-msg').textContent = 'Checking feed…';
  try { const r = await api('api/partiful', { url: $('#partiful-url').value.trim() }); $('#partiful-msg').textContent = `Saved. ${r.events} upcoming events found.`; setTimeout(load, 1500); }
  catch (err) { $('#partiful-msg').textContent = err.message; }
};
$('#partiful-clear').onclick = async () => { await api('api/partiful', { url: '' }); $('#partiful-url').value = ''; $('#partiful-msg').textContent = 'Removed.'; load(); };
$('#refresh').onclick = async () => { await api('api/refresh', {}); $('#status').textContent = 'Refreshing…'; setTimeout(load, 4000); };

async function pollUntilData() {
  for (let i = 0; i < 40; i++) {
    await new Promise(r => setTimeout(r, 2500));
    const s = await api('api/state');
    if (s.fetched_at && !s.refreshing) { render(s); return; }
    if (s.last_error) { $('#signin-msg').textContent = 'Luma error: ' + s.last_error; return; }
  }
  load();
}

// ---- data ----
async function load() { render(await api('api/state')); }

function render(s) {
  state.data = s;
  const showApp = s.signed_in && !state.showSources;
  $('#signin').hidden = showApp;
  $('#app').hidden = !showApp;
  $('#nav').hidden = !s.signed_in;
  $('#back-btn').hidden = !s.signed_in;
  $('#luma-status').textContent = s.luma_signed_in ? `Signed in as ${s.email}.` : 'Not signed in.';
  $('#email-form').hidden = s.luma_signed_in; $('#signout').hidden = !s.luma_signed_in;
  if (!s.luma_signed_in) { $('#code-form').hidden = true; $('#tfa-form').hidden = true; }
  if (s.partiful_url && !$('#partiful-url').value) $('#partiful-url').value = s.partiful_url;
  if (!showApp) return;
  s.calendars.forEach((c, i) => { c.color = c.tint_color && c.tint_color !== '#1e1e1e' ? c.tint_color : PALETTE[i % PALETTE.length]; });
  renderSidebar();
  renderMain();
  const when = s.fetched_at ? new Date(s.fetched_at).toLocaleString() : 'never';
  $('#status').textContent = `${s.events.length} events · updated ${when}` + (s.refreshing ? ' · refreshing…' : '') + (s.last_error ? ' · ' + s.last_error : '') + (s.errors.length ? ` · ${s.errors.length} calendar errors` : '');
}

function visibleEvents() {
  return state.data.events.filter(e => !state.hidden.has(e.calendar_api_id) && (!state.goingOnly || e.going));
}
function calOf(e) { return state.data.calendars.find(c => c.api_id === e.calendar_api_id) || {}; }

function renderSidebar() {
  const counts = {};
  state.data.events.forEach(e => { counts[e.calendar_api_id] = (counts[e.calendar_api_id] || 0) + 1; });
  const ul = $('#cal-list'); ul.innerHTML = '';
  [...state.data.calendars].sort((a, b) => (counts[b.api_id] || 0) - (counts[a.api_id] || 0)).forEach(c => {
    const li = document.createElement('li');
    li.className = state.hidden.has(c.api_id) ? 'off' : '';
    li.innerHTML = `<span class="swatch" style="background:${c.color}"></span>` + (c.avatar_url ? `<img src="${c.avatar_url}" alt="">` : '') + `<span class="name" title="${c.name}">${c.name}${c.not_followed ? ' *' : ''}</span><span class="count">${counts[c.api_id] || 0}</span>`;
    li.onclick = () => { state.hidden.has(c.api_id) ? state.hidden.delete(c.api_id) : state.hidden.add(c.api_id); renderSidebar(); renderMain(); };
    ul.appendChild(li);
  });
}
$('#all-cals').onclick = () => { state.hidden.clear(); renderSidebar(); renderMain(); };
$('#no-cals').onclick = () => { state.data.calendars.forEach(c => state.hidden.add(c.api_id)); renderSidebar(); renderMain(); };
$('#going-only').onchange = (e) => { state.goingOnly = e.target.checked; renderMain(); };
$('#view-toggle').onclick = () => { state.view = state.view === 'grid' ? 'list' : 'grid'; $('#view-toggle').textContent = state.view === 'grid' ? 'List' : 'Month'; renderMain(); };
$('#prev').onclick = () => { state.month = new Date(state.month.getFullYear(), state.month.getMonth() - 1, 1); renderMain(); };
$('#next').onclick = () => { state.month = new Date(state.month.getFullYear(), state.month.getMonth() + 1, 1); renderMain(); };
$('#today').onclick = () => { state.month = startOfMonth(new Date()); renderMain(); };

function renderMain() {
  $('#month-label').textContent = state.month.toLocaleDateString([], { month: 'long', year: 'numeric' });
  $('#grid').hidden = state.view !== 'grid';
  $('#list').hidden = state.view !== 'list';
  state.view === 'grid' ? renderGrid() : renderList();
}

function groupByDay(evs) {
  const by = {};
  evs.forEach(e => { if (!e.start_at) return; const k = dayKey(new Date(e.start_at)); (by[k] = by[k] || []).push(e); });
  return by;
}

function evNode(e, withDate) {
  const c = calOf(e);
  const el = document.createElement('div');
  el.className = 'ev' + (e.going ? ' going' : '');
  el.style.setProperty('--c', c.color || '');
  el.title = `${e.name} · ${c.name || ''}`;
  el.innerHTML = `<span class="t">${e.all_day ? 'all day' : fmtTime(e.start_at)}</span>${e.going ? '✓ ' : ''}${escapeHtml(e.name || '')}`;
  el.onclick = (ev) => showPopover(e, ev);
  return el;
}
function escapeHtml(s) { return s.replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch])); }

function renderGrid() {
  const g = $('#grid'); g.innerHTML = '';
  ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'].forEach(d => { const h = document.createElement('div'); h.className = 'dow'; h.textContent = d; g.appendChild(h); });
  const by = groupByDay(visibleEvents());
  const first = new Date(state.month); first.setDate(1 - first.getDay());
  const todayK = dayKey(new Date());
  for (let i = 0; i < 42; i++) {
    const d = new Date(first); d.setDate(first.getDate() + i);
    if (i === 35 && d.getMonth() !== state.month.getMonth()) break;
    const k = dayKey(d);
    const cell = document.createElement('div');
    cell.className = 'day' + (d.getMonth() !== state.month.getMonth() ? ' other' : '') + (k === todayK ? ' today' : '');
    cell.innerHTML = `<span class="num">${d.getDate()}</span>`;
    const evs = by[k] || [];
    const MAX = 5;
    evs.slice(0, MAX).forEach(e => cell.appendChild(evNode(e)));
    if (evs.length > MAX) { const m = document.createElement('div'); m.className = 'more'; m.textContent = `+${evs.length - MAX} more`; m.onclick = () => { state.view = 'list'; $('#view-toggle').textContent = 'Month'; renderMain(); document.getElementById('d-' + k)?.scrollIntoView(); }; cell.appendChild(m); }
    g.appendChild(cell);
  }
}

function renderList() {
  const l = $('#list'); l.innerHTML = '';
  const by = groupByDay(visibleEvents());
  const keys = Object.keys(by).sort().filter(k => k.startsWith(`${state.month.getFullYear()}-${String(state.month.getMonth()+1).padStart(2,'0')}`));
  if (!keys.length) l.innerHTML = '<p class="msg">No events this month.</p>';
  keys.forEach(k => {
    const h = document.createElement('div'); h.className = 'dayhead'; h.id = 'd-' + k; h.textContent = fmtDay(new Date(k + 'T12:00:00')); l.appendChild(h);
    by[k].sort((a, b) => a.start_at.localeCompare(b.start_at)).forEach(e => {
      const c = calOf(e);
      const row = document.createElement('div'); row.className = 'row'; row.style.setProperty('--c', c.color || '');
      row.innerHTML = `<div>${e.all_day ? 'all day' : fmtTime(e.start_at)}</div><div><div>${e.going ? '✓ ' : ''}${escapeHtml(e.name || '')}</div><div class="meta">${escapeHtml(c.name || '')}${e.city ? ' · ' + escapeHtml(e.city) : (e.location_type === 'online' ? ' · online' : '')}${e.hosts.length ? ' · ' + escapeHtml(e.hosts.join(', ')) : ''}</div></div>`;
      row.onclick = (ev) => showPopover(e, ev);
      l.appendChild(row);
    });
  });
}

function showPopover(e, ev) {
  ev.stopPropagation();
  const c = calOf(e);
  const p = $('#popover');
  const end = e.end_at ? ' – ' + fmtTime(e.end_at) : '';
  p.innerHTML = (e.cover_url ? `<img src="${e.cover_url}" alt="">` : '') + `<h3>${escapeHtml(e.name || '')}</h3><div class="meta">${fmtDay(new Date(e.start_at))} · ${fmtTime(e.start_at)}${end}<br>${escapeHtml(c.name || '')}${e.city ? ' · ' + escapeHtml(e.city) : (e.location_type === 'online' ? ' · online' : '')}${e.hosts.length ? '<br>Hosted by ' + escapeHtml(e.hosts.join(', ')) : ''}${e.going ? '<br>You are ' + escapeHtml(e.guest_status || 'registered') : ''}</div><a href="${e.url}" target="_blank" rel="noopener">Open on Luma ↗</a>`;
  p.hidden = false;
  const x = Math.min(ev.clientX, window.innerWidth - 360), y = Math.min(ev.clientY + 8, window.innerHeight - 300);
  p.style.left = Math.max(8, x) + 'px'; p.style.top = Math.max(8, y) + 'px';
}
document.addEventListener('click', (ev) => { if (!$('#popover').contains(ev.target)) $('#popover').hidden = true; });
document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') $('#popover').hidden = true; });

load();
setInterval(() => { if (state.data?.signed_in && document.visibilityState === 'visible') load(); }, 5 * 60 * 1000);
