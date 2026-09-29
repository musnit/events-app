// Bookmarklets run on luma.com / partiful.com in the user's own signed-in browser, collect what
// this app cannot fetch itself, and bring it back in the URL fragment (never sent to any server
// until the app posts it to its own API).
//
// Luma: the calendars you follow (paginated) and the events you registered for.
// Partiful: the Firebase refresh token from partiful.com's IndexedDB.

export function appRootUrl(): string {
  return new URL("./", document.baseURI).toString();
}

export function lumaBookmarklet(appUrl: string): string {
  return `javascript:(async()=>{try{if(!/(^|\\.)(luma\\.com|lu\\.ma)$/.test(location.hostname)){alert('Open luma.com first (signed in), then tap this bookmark.');return;}const g=async(p)=>{const r=await fetch('https://api.luma.com'+p,{credentials:'include'});if(!r.ok)throw new Error('Luma said '+r.status+' (signed in?)');return r.json();};const cals=[];let cur=null;for(let i=0;i<20;i++){const d=await g('/home/get-following-calendars?pagination_limit=100'+(cur?'&pagination_cursor='+encodeURIComponent(cur):''));const walk=(o)=>{if(Array.isArray(o))o.forEach(walk);else if(o&&typeof o==='object'){if(typeof o.api_id==='string'&&o.api_id.startsWith('cal-')&&'name'in o)cals.push({api_id:o.api_id,name:o.name,slug:o.slug||null,avatar_url:o.avatar_url||null,tint_color:o.tint_color||null,description_short:o.description_short||null,is_personal:!!o.is_personal,personal_user:o.personal_user?{name:o.personal_user.name}:null});else Object.values(o).forEach(walk);}};walk(d);if(!d.has_more||!d.next_cursor)break;cur=d.next_cursor;}const going=[];try{let c2=null;for(let i=0;i<10;i++){const e=await g('/home/get-events?period=future&pagination_limit=100'+(c2?'&pagination_cursor='+encodeURIComponent(c2):''));(e.entries||[]).forEach(x=>x.event&&going.push(x.event.api_id));if(!e.has_more||!e.next_cursor)break;c2=e.next_cursor;}}catch(_){}const m=document.cookie.match(/(?:^|;\\s*)luma\\.auth-session-key=([^;]+)/);const payload={calendars:cals,going,session_key:m?decodeURIComponent(m[1]):null};if(!cals.length){alert('Found no followed calendars. Are you signed in to luma.com?');return;}location.href=${JSON.stringify(appUrl)}+'#import='+encodeURIComponent(btoa(unescape(encodeURIComponent(JSON.stringify(payload)))));}catch(e){alert('Import failed: '+e.message);}})()`;
}

export function partifulBookmarklet(appUrl: string): string {
  return `javascript:(async()=>{try{if(!/(^|\\.)partiful\\.com$/.test(location.hostname)){alert('Open partiful.com first (signed in), then tap this bookmark.');return;}const db=await new Promise((res,rej)=>{const r=indexedDB.open('firebaseLocalStorageDb');r.onsuccess=()=>res(r.result);r.onerror=()=>rej(r.error);});const rows=await new Promise((res,rej)=>{const tx=db.transaction('firebaseLocalStorage','readonly');const q=tx.objectStore('firebaseLocalStorage').getAll();q.onsuccess=()=>res(q.result);q.onerror=()=>rej(q.error);});const row=rows.find(r=>String(r.fbase_key||'').startsWith('firebase:authUser:'));if(!row||!row.value||!row.value.stsTokenManager){alert('You are not signed in to Partiful in this browser.');return;}const v=row.value;const payload={uid:v.uid,refresh_token:v.stsTokenManager.refreshToken};location.href=${JSON.stringify(appUrl)}+'#pfimport='+encodeURIComponent(btoa(unescape(encodeURIComponent(JSON.stringify(payload)))));}catch(e){alert('Import failed: '+e.message);}})()`;
}

export interface ImportRequest {
  kind: "luma" | "partiful";
  payload: unknown;
}

/** Read a bookmarklet hand-off from the URL fragment (#import=… or #pfimport=…). */
export function readImportHash(hash: string): ImportRequest | null {
  const match = /^#(import|pfimport)=(.+)$/.exec(hash);
  if (!match) return null;
  try {
    const bytes = atob(decodeURIComponent(match[2]));
    const text = decodeURIComponent(Array.from(bytes, (c) => `%${c.charCodeAt(0).toString(16).padStart(2, "0")}`).join(""));
    return { kind: match[1] === "import" ? "luma" : "partiful", payload: JSON.parse(text) };
  } catch {
    return null;
  }
}
