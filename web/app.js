// Luma followed-calendars view. Talks to the local server with relative URLs so it works under /luma/.
const $ = (s) => document.querySelector(s);
const state = { data: null, month: startOfMonth(new Date()), day: new Date(), hidden: new Set(JSON.parse(localStorage.getItem('hidden') || '[]')), goingOnly: localStorage.getItem('goingOnly') === '1', area: localStorage.getItem('area') || 'bay', view: 'list', email: '' };
function saveHidden() { localStorage.setItem('hidden', JSON.stringify([...state.hidden])); }
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
$('#session-form').onsubmit = async (e) => {
  e.preventDefault();
  $('#signin-msg').textContent = 'Checking the session with Luma…';
  try { await api('api/auth/session', { session_key: $('#session-key').value.trim() }); $('#session-key').value = ''; $('#signin-msg').textContent = 'Signed in. Loading your calendars…'; state.showSources = false; pollUntilData(); }
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
  $('#luma-status').textContent = s.luma_signed_in ? `Luma session active (${s.email}); followed list syncs automatically.` : '';
  $('#email-form').hidden = s.luma_signed_in; $('#session-form').hidden = s.luma_signed_in; $('#signout').hidden = !s.luma_signed_in;
  if (!s.luma_signed_in) { $('#code-form').hidden = true; $('#tfa-form').hidden = true; }
  if (s.partiful_url && !$('#partiful-url').value) $('#partiful-url').value = s.partiful_url;
  if (s.luma_ics_url && !$('#luma-ics-url').value) $('#luma-ics-url').value = s.luma_ics_url;
  renderManual(s.luma_manual || []);
  $('#pf-status').textContent = s.partiful_connected ? 'Partiful is connected; following and your own events sync automatically.' : '';
  $('#pf-disconnect').hidden = !s.partiful_connected;
  if (!showApp) return;
  // Colour by source, not per calendar: per-calendar colours carry no meaning with 100+ calendars.
  const SOURCE_COLORS = { luma: PALETTE[0], partiful: '#ff5c8a', agihouse: '#2dd4bf' };
  s.calendars.forEach(c => { c.color = SOURCE_COLORS[c.source] || PALETTE[0]; });
  renderSidebar();
  renderMain();
  const when = s.fetched_at ? new Date(s.fetched_at).toLocaleString() : 'never';
  $('#status').textContent = `${s.events.length} events · updated ${when}` + (s.refreshing ? ' · refreshing…' : '') + (s.last_error ? ' · ' + s.last_error : '') + (s.errors.length ? ` · ${s.errors.length} calendar errors` : '');
}

function inArea(e) {
  if (state.area === 'all') return true;
  const a = e.area || 'unknown';
  // Unknown-location events in Pacific time are most likely local, so they stay in the Bay Area view.
  const likelyLocal = a === 'unknown' && /America\/Los_Angeles|US\/Pacific/.test(e.timezone || '');
  if (a === 'bay' || likelyLocal) return true;
  return state.area === 'bay+online' && a === 'online';
}
function visibleEvents() {
  return state.data.events.filter(e => inArea(e) && !state.hidden.has(e.calendar_api_id) && (!state.goingOnly || e.going));
}
function calOf(e) { return state.data.calendars.find(c => c.api_id === e.calendar_api_id) || {}; }

function renderSidebar() {
  const counts = {};
  state.data.events.filter(inArea).forEach(e => { counts[e.calendar_api_id] = (counts[e.calendar_api_id] || 0) + 1; });
  const ul = $('#cal-list'); ul.innerHTML = '';
  [...state.data.calendars].sort((a, b) => (a.name || '').localeCompare(b.name || '', undefined, { sensitivity: 'base' })).forEach(c => {
    const li = document.createElement('li');
    li.className = state.hidden.has(c.api_id) ? 'off' : '';
    li.innerHTML = (c.avatar_url ? `<img src="${imgUrl(c.avatar_url, 18)}" alt="" loading="lazy">` : '') + `<span class="name" title="${c.name}">${c.name}${c.not_followed ? ' *' : ''}</span><span class="count">${counts[c.api_id] || 0}</span>`;
    li.onclick = () => { state.hidden.has(c.api_id) ? state.hidden.delete(c.api_id) : state.hidden.add(c.api_id); saveHidden(); renderSidebar(); renderMain(); };
    ul.appendChild(li);
  });
}
$('#all-cals').onclick = () => { state.hidden.clear(); saveHidden(); renderSidebar(); renderMain(); };
$('#no-cals').onclick = () => { state.data.calendars.forEach(c => state.hidden.add(c.api_id)); saveHidden(); renderSidebar(); renderMain(); };
$('#going-only').checked = state.goingOnly;
$('#going-only').onchange = (e) => { state.goingOnly = e.target.checked; localStorage.setItem('goingOnly', state.goingOnly ? '1' : '0'); renderMain(); };
$('#area').value = state.area;
$('#area').onchange = (e) => { state.area = e.target.value; localStorage.setItem('area', state.area); renderSidebar(); renderMain(); };
function setView(v) { state.view = v; localStorage.setItem('view', v); ['day','grid','list'].forEach(x => $('#view-' + x).classList.toggle('on', x === v)); renderMain(); }
$('#view-grid').onclick = () => { state.month = startOfMonth(new Date()); setView('grid'); };
$('#view-list').onclick = () => { state.month = startOfMonth(new Date()); setView('list'); };
$('#view-day').onclick = () => { state.day = new Date(); setView('day'); };
function step(n) {
  if (state.view === 'day') { const d = new Date(state.day); d.setDate(d.getDate() + n); state.day = d; }
  else state.month = new Date(state.month.getFullYear(), state.month.getMonth() + n, 1);
  renderMain();
}
$('#prev').onclick = () => step(-1);
$('#next').onclick = () => step(1);

function renderMain() {
  const isDay = state.view === 'day';
  const todayK = dayKey(new Date());
  $('#month-label').textContent = isDay
    ? (dayKey(state.day) === todayK ? 'Today, ' : '') + fmtDay(state.day)
    : state.month.toLocaleDateString([], { month: 'long', year: 'numeric' });
  $('#grid').hidden = state.view !== 'grid';
  $('#list').hidden = state.view === 'grid';
  if (state.view === 'grid') renderGrid(); else if (isDay) renderDay(); else renderList();
}

function renderDay() {
  const l = $('#list'); l.innerHTML = '';
  const evs = (groupByDay(visibleEvents())[dayKey(state.day)] || []).sort((a, b) => a.start_at.localeCompare(b.start_at));
  if (!evs.length) { l.innerHTML = '<p class="empty">Nothing on this day from the calendars you have turned on.</p>'; return; }
  l.classList.remove('list-mode'); l.classList.add('day-mode');
  evs.forEach(e => l.appendChild(dayCard(e)));
}

// Ask the CDNs for images sized for the card instead of the multi-megabyte originals.
function imgUrl(u, px) {
  if (!u) return u;
  try {
    const url = new URL(u);
    if (url.hostname === 'images.lumacdn.com' && !url.pathname.startsWith('/cdn-cgi/')) {
      return `https://images.lumacdn.com/cdn-cgi/image/format=auto,fit=cover,dpr=2,quality=75,width=${px},height=${px}${url.pathname}`;
    }
    if (url.hostname === 'cdn.lu.ma' && !url.pathname.startsWith('/cdn-cgi/')) {
      return `https://cdn.lu.ma/cdn-cgi/image/format=auto,fit=cover,dpr=2,quality=75,width=${px},height=${px}${url.pathname}`;
    }
    if (url.hostname.endsWith('imgix.net')) {
      url.searchParams.set('w', String(px * 2)); url.searchParams.set('h', String(px * 2)); url.searchParams.set('fit', 'crop'); url.searchParams.set('auto', 'format,compress');
      return url.toString();
    }
  } catch { /* leave odd URLs alone */ }
  return u;
}
function hostAvatars(e) {
  return (e.host_avatars || []).slice(0, 3).filter(Boolean).map(u => `<img class="av" src="${imgUrl(u, 32)}" alt="" loading="lazy" decoding="async">`).join('');
}
function dayCard(e) {
  const c = calOf(e);
  const card = document.createElement('div'); card.className = 'card'; card.style.setProperty('--c', c.color || '');
  const end = e.end_at ? ' – ' + fmtTime(e.end_at) : '';
  const where = e.city ? escapeHtml(e.city) : (e.area === 'online' || e.location_type === 'online' ? 'online' : '');
  // The calendar is the group or community, so it leads; individual hosts follow, org-looking ones first.
  // Luma's "presented by" is the event's own calendar, which can differ from the followed calendar that listed it.
  const pb = e.presented_by && e.presented_by.name ? e.presented_by : c;
  const calName = (pb.name || '').trim().toLowerCase();
  const people = e.hosts.map((n, i) => ({ n, a: (e.host_avatars || [])[i] })).filter(h => h.n.trim().toLowerCase() !== calName);
  const avs = [pb.avatar_url, ...people.slice(0, 2).map(h => h.a)].filter(Boolean).map(u => `<img class="av" src="${imgUrl(u, 32)}" alt="" loading="lazy" decoding="async">`).join('');
  const names = [pb.name, ...people.slice(0, 2).map(h => h.n)].filter(Boolean).join(' · ') + (people.length > 2 ? ` +${people.length - 2}` : '');
  const hostLine = `<div class="hosts">${avs}<span>${escapeHtml(names)}</span></div>`;
  const fallbackAv = pb.avatar_url || c.avatar_url || '';
  const placeholder = `<div class="noimg">${fallbackAv ? `<img class="ph" src="${imgUrl(fallbackAv, 64)}" alt="">` : ''}</div>`;
  card.innerHTML = (e.cover_url ? `<img src="${imgUrl(e.cover_url, 160)}" alt="" loading="lazy" decoding="async" onerror="this.outerHTML=this.dataset.ph" data-ph="${escapeHtml(placeholder)}">` : placeholder) +
    `<div class="body">${hostLine}<div class="when">${e.all_day ? 'All day' : fmtTime(e.start_at) + end}</div>` +
    `<div class="title">${e.going ? '✓ ' : ''}${escapeHtml(e.name || '')}</div>` +
    (where ? `<div class="meta">${where}</div>` : '') + `</div>`;
  card.onclick = (ev) => showPopover(e, ev);
  return card;
}

function listRow(e) {
  const c = calOf(e);
  const row = document.createElement('div'); row.className = 'row'; row.style.setProperty('--c', c.color || '');
  row.innerHTML = `<div>${e.all_day ? 'all day' : fmtTime(e.start_at)}</div><div><div>${e.going ? '✓ ' : ''}${escapeHtml(e.name || '')}</div><div class="meta">${escapeHtml(c.name || '')}${e.city ? ' · ' + escapeHtml(e.city) : (e.location_type === 'online' ? ' · online' : '')}${e.hosts.length ? ' · ' + escapeHtml(e.hosts.join(', ')) : ''}</div></div>`;
  row.onclick = (ev) => showPopover(e, ev);
  return row;
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
    if (evs.length > MAX) { const m = document.createElement('div'); m.className = 'more'; m.textContent = `+${evs.length - MAX} more`; m.onclick = () => { state.day = new Date(k + 'T12:00:00'); setView('day'); }; cell.appendChild(m); }
    g.appendChild(cell);
  }
}

function renderList() {
  const l = $('#list'); l.innerHTML = ''; l.classList.remove('day-mode'); l.classList.add('list-mode');
  const by = groupByDay(visibleEvents());
  const keys = Object.keys(by).sort().filter(k => k.startsWith(`${state.month.getFullYear()}-${String(state.month.getMonth()+1).padStart(2,'0')}`));
  if (!keys.length) l.innerHTML = '<p class="empty">No events this month.</p>';
  keys.forEach(k => {
    const h = document.createElement('div'); h.className = 'dayhead'; h.id = 'd-' + k; h.textContent = fmtDay(new Date(k + 'T12:00:00')); l.appendChild(h);
    const wrap = document.createElement('div'); wrap.className = 'cards';
    by[k].sort((a, b) => a.start_at.localeCompare(b.start_at)).forEach(e => wrap.appendChild(dayCard(e)));
    l.appendChild(wrap);
  });
}

function showPopover(e, ev) {
  ev.stopPropagation();
  const c = calOf(e);
  const p = $('#popover');
  const end = e.end_at ? ' – ' + fmtTime(e.end_at) : '';
  p.innerHTML = (e.cover_url ? `<img src="${imgUrl(e.cover_url, 340)}" alt="">` : '') + `<h3>${escapeHtml(e.name || '')}</h3><div class="meta">${fmtDay(new Date(e.start_at))} · ${fmtTime(e.start_at)}${end}<br>${escapeHtml((e.presented_by && e.presented_by.name) || c.name || '')}${e.city ? ' · ' + escapeHtml(e.city) : (e.location_type === 'online' ? ' · online' : '')}${e.hosts.length ? '<br>' + hostAvatars(e) + 'Hosted by ' + escapeHtml(e.hosts.join(', ')) : ''}${e.going ? '<br>You are ' + escapeHtml(e.guest_status || 'registered') : ''}</div><a href="${e.url}" target="_blank" rel="noopener">Open on Luma ↗</a>`;
  p.hidden = false;
  // Measure the real popover, then open it below the tap if it fits, otherwise above it.
  const w = p.offsetWidth, h = p.offsetHeight;
  const x = Math.max(8, Math.min(ev.clientX, window.innerWidth - w - 8));
  let y = ev.clientY + 8;
  if (y + h > window.innerHeight - 8) y = ev.clientY - h - 8;
  if (y < 8) y = Math.max(8, window.innerHeight - h - 8);
  p.style.left = x + 'px'; p.style.top = y + 'px';
}
document.addEventListener('click', (ev) => { if (!$('#popover').contains(ev.target)) $('#popover').hidden = true; });
document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') $('#popover').hidden = true; });

const APP_URL = location.origin + location.pathname.replace(/[^/]*$/, '');
const BOOKMARKLET = `javascript:(async()=>{try{if(!/(^|\\.)(luma\\.com|lu\\.ma)$/.test(location.hostname)){alert('Open luma.com first (signed in), then tap this bookmark.');return;}const g=async(p)=>{const r=await fetch('https://api.luma.com'+p,{credentials:'include'});if(!r.ok)throw new Error('Luma said '+r.status+' (signed in?)');return r.json();};const cals=[];let cur=null;for(let i=0;i<20;i++){const d=await g('/home/get-following-calendars?pagination_limit=100'+(cur?'&pagination_cursor='+encodeURIComponent(cur):''));const walk=(o)=>{if(Array.isArray(o))o.forEach(walk);else if(o&&typeof o==='object'){if(typeof o.api_id==='string'&&o.api_id.startsWith('cal-')&&'name'in o)cals.push({api_id:o.api_id,name:o.name,slug:o.slug||null,avatar_url:o.avatar_url||null,tint_color:o.tint_color||null,is_personal:!!o.is_personal,personal_user:o.personal_user?{name:o.personal_user.name}:null});else Object.values(o).forEach(walk);}};walk(d);if(!d.has_more||!d.next_cursor)break;cur=d.next_cursor;}const going=[];try{const e=await g('/home/get-events?period=future&pagination_limit=100');(e.entries||[]).forEach(x=>x.event&&going.push(x.event.api_id));}catch(_){}const m=document.cookie.match(/(?:^|;\\s*)luma\\.auth-session-key=([^;]+)/);const payload={calendars:cals,going,session_key:m?decodeURIComponent(m[1]):null};if(!cals.length){alert('Found no followed calendars. Are you signed in to luma.com?');return;}location.href=${JSON.stringify(APP_URL)}+'#import='+encodeURIComponent(btoa(unescape(encodeURIComponent(JSON.stringify(payload)))));}catch(e){alert('Import failed: '+e.message);}})()`;
$('#bookmarklet').href = BOOKMARKLET;
const PF_BOOKMARKLET = `javascript:(async()=>{try{if(!/(^|\\.)partiful\\.com$/.test(location.hostname)){alert('Open partiful.com first (signed in), then tap this bookmark.');return;}const db=await new Promise((res,rej)=>{const r=indexedDB.open('firebaseLocalStorageDb');r.onsuccess=()=>res(r.result);r.onerror=()=>rej(r.error);});const rows=await new Promise((res,rej)=>{const tx=db.transaction('firebaseLocalStorage','readonly');const q=tx.objectStore('firebaseLocalStorage').getAll();q.onsuccess=()=>res(q.result);q.onerror=()=>rej(q.error);});const row=rows.find(r=>String(r.fbase_key||'').startsWith('firebase:authUser:'));if(!row||!row.value||!row.value.stsTokenManager){alert('You are not signed in to Partiful in this browser.');return;}const v=row.value;const payload={uid:v.uid,refresh_token:v.stsTokenManager.refreshToken};location.href=${JSON.stringify(APP_URL)}+'#pfimport='+encodeURIComponent(btoa(unescape(encodeURIComponent(JSON.stringify(payload)))));}catch(e){alert('Import failed: '+e.message);}})()`;
$('#pf-bookmarklet').href = PF_BOOKMARKLET;
$('#pf-bm-text').value = PF_BOOKMARKLET;
async function copyPfBookmarklet() {
  try { await navigator.clipboard.writeText(PF_BOOKMARKLET); $('#copy-pf-bookmarklet').textContent = 'Copied ✓'; $('#pf-import-msg').textContent = 'Copied. Now follow the steps above.'; }
  catch { $('#pf-bm-text').hidden = false; $('#pf-bm-text').select(); $('#pf-import-msg').textContent = 'Select all of the text above and copy it.'; }
}
$('#copy-pf-bookmarklet').onclick = copyPfBookmarklet;
$('#pf-bookmarklet').onclick = (e) => { e.preventDefault(); copyPfBookmarklet(); };
$('#pf-disconnect').onclick = async () => { await api('api/partiful/disconnect', {}); load(); };
$('#bookmarklet').onclick = (e) => { e.preventDefault(); copyBookmarklet(); };
async function copyBookmarklet() {
  try { await navigator.clipboard.writeText(BOOKMARKLET); $('#copy-bookmarklet').textContent = 'Copied ✓'; $('#import-msg').textContent = 'Copied. Now follow the steps below.'; }
  catch { $('#bm-text').hidden = false; $('#bm-text').value = BOOKMARKLET; $('#bm-text').select(); $('#import-msg').textContent = 'Select all of the text above and copy it.'; }
}
$('#copy-bookmarklet').onclick = copyBookmarklet;
$('#bm-text').value = BOOKMARKLET;
(function pickPlatform() {
  const ua = navigator.userAgent;
  const which = /iPhone|iPad/.test(ua) ? 'ios' : /Android/.test(ua) ? 'android' : 'desktop';
  document.querySelectorAll('[data-platform]').forEach(el => { el.hidden = el.dataset.platform !== which; });
  document.querySelectorAll('.platform-tabs button').forEach(b => { b.classList.toggle('on', b.dataset.pick === which); b.onclick = () => { document.querySelectorAll('[data-platform]').forEach(el => { el.hidden = el.dataset.platform !== b.dataset.pick; }); document.querySelectorAll('.platform-tabs button').forEach(x => x.classList.toggle('on', x === b)); }; });
})();

$('#luma-links-form').onsubmit = async (e) => {
  e.preventDefault();
  $('#luma-links-msg').textContent = 'Looking up calendars…';
  try {
    const r = await api('api/luma/calendars', { text: $('#luma-links').value });
    $('#luma-links').value = '';
    $('#luma-links-msg').textContent = `Added ${r.added.length}` + (r.failed.length ? `. Skipped: ${r.failed.join('; ')}` : '.');
    setTimeout(load, 1500);
  } catch (err) { $('#luma-links-msg').textContent = err.message; }
};
$('#luma-ics-form').onsubmit = async (e) => {
  e.preventDefault();
  $('#luma-ics-msg').textContent = 'Checking feed…';
  try { const r = await api('api/luma/ics', { url: $('#luma-ics-url').value.trim() }); $('#luma-ics-msg').textContent = `Saved. ${r.events} upcoming events you're going to.`; setTimeout(load, 1500); }
  catch (err) { $('#luma-ics-msg').textContent = err.message; }
};
$('#luma-ics-clear').onclick = async () => { await api('api/luma/ics', { url: '' }); $('#luma-ics-url').value = ''; $('#luma-ics-msg').textContent = 'Removed.'; load(); };

function renderManual(list) {
  const ul = $('#luma-manual'); ul.innerHTML = '';
  list.forEach(c => {
    const li = document.createElement('li');
    li.innerHTML = (c.avatar_url ? `<img src="${imgUrl(c.avatar_url, 18)}" alt="" loading="lazy">` : '') + `<span>${escapeHtml(c.name)}</span><button title="remove">✕</button>`;
    li.querySelector('button').onclick = async () => { await api('api/luma/calendars/remove', { api_id: c.api_id }); load(); };
    ul.appendChild(li);
  });
}

async function handleImportHash() {
  const pm = location.hash.match(/^#pfimport=(.+)$/);
  if (pm) {
    history.replaceState(null, '', location.pathname);
    state.showSources = true;
    try {
      const payload = JSON.parse(decodeURIComponent(escape(atob(decodeURIComponent(pm[1])))));
      await api('api/partiful/import', { payload });
      await load();
      $('#pf-import-msg').textContent = 'Partiful connected. Pulling the events from people you follow…';
      setTimeout(load, 6000);
    } catch (err) { await load(); $('#pf-import-msg').textContent = 'Partiful import failed: ' + err.message; }
    return true;
  }
  const m = location.hash.match(/^#import=(.+)$/);
  if (!m) return false;
  history.replaceState(null, '', location.pathname);
  try {
    const payload = JSON.parse(decodeURIComponent(escape(atob(decodeURIComponent(m[1])))));
    const r = await api('api/luma/import', { payload });
    state.showSources = true;
    await load();
    $('#import-msg').textContent = `Imported ${r.added} new calendars from your Luma follows (${r.total} total).` + (r.session ? ' Your Luma session was captured too, so new follows will sync on their own.' : '');
  } catch (err) { state.showSources = true; await load(); $('#import-msg').textContent = 'Import failed: ' + err.message; }
  return true;
}

// Opens on the last used view (List the first time); ?view=day|month|list overrides it.
const qv = new URLSearchParams(location.search).get('view');
const initialView = ({ month: 'grid' })[qv] || qv || localStorage.getItem('view') || 'list';
state.view = ['day','grid','list'].includes(initialView) ? initialView : 'list';
['day','grid','list'].forEach(x => $('#view-' + x).classList.toggle('on', x === state.view));
handleImportHash().then(done => { if (!done) load(); });
setInterval(() => { if (state.data?.signed_in && document.visibilityState === 'visible') load(); }, 5 * 60 * 1000);
