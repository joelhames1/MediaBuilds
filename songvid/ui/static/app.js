/* Cuesheet front end. Plain JS, no build step. Talks to songvid/ui/server.py. */
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const h = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') e.className = v; else if (k === 'style') e.style.cssText = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'value') e.value = v; else e.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat(Infinity)) if (k != null && k !== false) e.append(k.nodeType ? k : document.createTextNode(String(k)));
  return e;
};
const fmt = t => { t = Math.max(0, t || 0); const m = Math.floor(t / 60), s = t - m * 60; return `${String(m).padStart(2, '0')}:${s.toFixed(2).padStart(5, '0')}`; };
const clone = o => JSON.parse(JSON.stringify(o));
const esc = s => String(s ?? '');

const STAGES = [['song', 'Song'], ['suno', 'Suno'], ['timing', 'Timing'], ['board', 'Board'], ['look', 'Look'], ['picture', 'Picture'], ['render', 'Render']];
const SCENES = ['nebula', 'smoke', 'rays', 'particles', 'waves', 'embers'];

const S = {
  route: 'library', slug: null, stage: 'board', P: null, keys: null,
  t: 0, playing: false, sel: null, slice: null, pickWord: null, tap: null,
  chats: {}, songDraft: null, songBase: null, artDraft: null, open: {}, boardMode: 'claude', renderSel: null, stamp: 0,
};

// ---------------- api ----------------
async function api(method, url, body, isForm) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) opt.body = body; else { opt.body = JSON.stringify(body); opt.headers['Content-Type'] = 'application/json'; }
  }
  const r = await fetch(url, opt);
  let data = null; try { data = await r.json(); } catch { }
  if (!r.ok) { const msg = (data && (data.detail || data.message)) || `Request failed (${r.status})`; const e = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg)); e.status = r.status; throw e; }
  return data;
}
const P = () => S.P;
const fileUrl = (path, bust = true) => `/files/${S.slug}/${path}${bust ? `?v=${Math.floor(S.stamp)}` : ''}`;
function toast(text, bad) { const t = h('div', { class: 'toast', style: bad ? 'border-color:var(--bad)' : '' }, text); $('#toasts').append(t); setTimeout(() => t.remove(), bad ? 7000 : 4200); }
async function run(kind, args = {}) {
  const btn = document.activeElement?.tagName === 'BUTTON' ? document.activeElement : null;
  if (btn) btn.disabled = true;  // re-enabled by the re-render that follows
  try { await api('POST', `/api/projects/${S.slug}/jobs`, { kind, args }); await refresh(); }
  catch (e) { toast(e.message, true); }
}

// ---------------- routing ----------------
function parseRoute() {
  const m = location.hash.match(/^#\/p\/([a-z0-9-]+)(?:\/(\w+))?/);
  if (m) { S.route = 'project'; if (S.slug !== m[1]) { S.slug = m[1]; S.P = null; S.sel = null; S.t = 0; S.songDraft = null; setPlay(false); } S.stage = m[2] || S.stage || 'board'; }
  else if (location.hash.startsWith('#/settings')) S.route = 'settings';
  else S.route = 'library';
}
window.addEventListener('hashchange', () => { parseRoute(); boot(); });
const go = stage => { location.hash = `#/p/${S.slug}/${stage}`; };

async function boot() {
  $('#libView').hidden = S.route !== 'library'; $('#setView').hidden = S.route !== 'settings'; $('#projView').hidden = S.route !== 'project';
  $('#crumb').hidden = $('#strip').hidden = $('#statusLine').hidden = S.route !== 'project';
  if (S.route === 'library') return renderLib();
  if (S.route === 'settings') return renderSettings();
  await refresh(true);
}

async function refresh(full) {
  if (S.route !== 'project' || !S.slug) return;
  let d;
  try { d = await api('GET', `/api/projects/${S.slug}`); } catch (e) { toast(e.message, true); location.hash = '#/'; return; }
  const first = !S.P;
  S.P = d; S.stamp = d.stamp;
  const aud = $('#aud');
  const src = d.audio ? fileUrl(d.audio) : '';
  if (d.audio && !aud.src.includes(`/files/${S.slug}/${d.audio}`)) { aud.src = src; }
  if (!d.audio) aud.removeAttribute('src');
  if (!S.sel && d.storyboard) S.sel = d.storyboard.shots[0]?.id;
  if (first && !location.hash.match(/\/p\/[^/]+\/\w+/)) S.stage = firstOpen(d);
  renderAll(full || first);
}
function firstOpen(d) { const st = d.status; for (const [id] of STAGES) if (['needs', 'stale', 'empty'].includes(st[id].state)) return id === 'picture' ? 'look' : id; return 'render'; }

// ---------------- events ----------------
let refreshTimer = null;
function connectEvents() {
  const es = new EventSource('/api/events');
  es.onmessage = ev => {
    const e = JSON.parse(ev.data);
    if (e.type === 'job' && e.job.slug === S.slug && S.P) {
      const i = S.P.jobs.findIndex(j => j.id === e.job.id);
      if (i >= 0) S.P.jobs[i] = e.job; else S.P.jobs.unshift(e.job);
      renderJobs();
      if (['queued', 'done', 'error'].includes(e.job.status)) {  // job started or ended: refresh button states
        if (!editing($('#stagePane'))) renderStage();
        if (!editing($('#inspector'))) renderInspector();
      }
      if (e.job.status === 'error') toast(`${e.job.label} failed: ${e.job.error}`, true);
      if (e.job.status === 'done') toast(`${e.job.label}: done`);
    }
    if (e.type === 'project' && e.slug === S.slug) { clearTimeout(refreshTimer); refreshTimer = setTimeout(() => refresh(), 150); }
  };
}

// ---------------- render all ----------------
function editing(el) { const a = document.activeElement; return a && el && el.contains(a) && a.matches('input, textarea, select'); }
function renderAll(force) {
  const d = P(); if (!d) return;
  $('#songTitle').textContent = d.song?.title || d.slug;
  const an = d.analysis;
  $('#songMeta').textContent = an ? `${fmt(an.duration).slice(0, 5)} · ${Math.round(an.tempo)} BPM` : '';
  $('#bpm').textContent = an ? `${Math.round(an.tempo)} BPM · ${an.downbeats.length} bars` : '';
  renderStrip();
  if (force || !editing($('#stagePane'))) renderStage();
  if (force || !editing($('#inspector'))) renderInspector();
  renderTimeline(); renderJobs(); renderHist(); renderChat();
}

// ---------------- strip ----------------
function renderStrip() {
  const d = P(); const st = $('#strip'); st.innerHTML = '';
  STAGES.forEach(([id, name]) => st.append(h('button', { class: `seg ${d.status[id].state}`, 'aria-current': S.stage === id ? 'step' : false, title: d.status[id].text, onclick: () => go(id) },
    h('span', { class: 'g' }), h('span', { class: 'n' }, name))));
  const line = $('#statusLine'); line.innerHTML = '';
  const stale = STAGES.find(([id]) => d.status[id].state === 'stale');
  const need = STAGES.find(([id]) => d.status[id].state === 'needs');
  const cur = d.status[S.stage];
  if (stale) line.append(h('strong', {}, `${stale[1]} is out of date.`), ' ', d.status[stale[0]].text,
    stale[0] === 'render' ? h('button', { class: 'btn', onclick: () => go('render') }, 'Open Render') : h('button', { class: 'btn primary', onclick: () => run('rebuild') }, `Rebuild from ${stale[1]}`));
  else if (need) line.append(...[h('strong', {}, `${need[1]} needs you:`), ' ', d.status[need[0]].text, need[0] !== S.stage ? h('button', { class: 'btn', onclick: () => go(need[0]) }, `Open ${need[1]}`) : null].filter(x => x != null));
  else line.append(cur.text);
}

// ---------------- helpers ----------------
const shots = () => P().storyboard?.shots || [];
const lines = () => P().timing?.lines || [];
const words = () => P().timing?.words || [];
const dur = () => P().analysis?.duration || P().timing?.duration || 0;
function shotAt(t) { let s = shots()[0]; for (const x of shots()) if (x.start <= t) s = x; return s; }
function lineAllowed(li) { const sh = shotAt(lines()[li].start); return sh ? sh.lyrics_overlay : true; }
function lyricStyle() {
  const st = P().storyboard?.lyric_style || 'auto'; if (st !== 'auto') return st;
  const src = P().timing?.source || ''; return src.includes('line-level') && !src.includes('Whisper') ? 'lines' : 'words';
}
function lineWindow(li) {
  const L = lines(), ln = L[li], nx = L[li + 1];
  if (lyricStyle() === 'lines') { let e = ln.end + 1.6; if (nx) e = Math.min(e, nx.start - 0.12); const s = ln.start - 0.12; return [s, Math.max(e, s + 0.8)]; }
  let e = ln.end + 0.7; if (nx) e = Math.min(e, nx.start - 0.35 + 0.125); return [ln.start - 0.35, Math.max(e, ln.end + 0.1)];
}
function shotImg(sh, big) { const f = P().shot_files[sh.id] || {}; return f.key && (sh.source === 'generated' || sh.source === 'claude') ? fileUrl(f.key) : f.thumb ? fileUrl(f.thumb) : null; }
const snap = t => { const g = P().analysis?.downbeats || []; return g.length ? g.reduce((a, b) => Math.abs(b - t) < Math.abs(a - t) ? b : a, g[0]) : t; };
const busy = kind => (P()?.jobs || []).some(j => j.kind === kind && ['queued', 'running'].includes(j.status));
const head = (label, ...right) => h('div', { class: 'pane-h' }, h('span', { class: 'label' }, label), ...right);
const place = (title, text, ...actions) => h('div', { class: 'place' }, h('b', {}, title), h('span', {}, text), h('div', { class: 'wrap-gap' }, ...actions));
function copyText(t) {
  try { navigator.clipboard.writeText(t).then(() => toast('Copied. Paste it into Suno.'), () => toast('Copy was blocked. Select the text and copy it.', true)); }
  catch { toast('Copy was blocked. Select the text and copy it.', true); }
}
function keySet(name) { return (S.keys || []).some(k => k.name === name && k.set); }
async function loadKeys() { try { S.keys = (await api('GET', '/api/keys')).keys; } catch { } const miss = (S.keys || []).filter(k => !k.set && k.name !== 'SUNOAPI_KEY').length; $('#keysBtn').textContent = miss ? `API keys (${miss} missing)` : 'API keys'; }

function monitor() {
  const sh = shotAt(S.t); const img = sh ? shotImg(sh, true) : null;
  return h('div', { class: 'monitor' },
    img ? h('img', { src: img, alt: '', id: 'monImg' }) : h('div', { class: 'place', style: 'position:absolute;inset:0;color:#aaa' }, 'Render stills to see the look here.'),
    P().storyboard?.letterbox !== false ? h('div', { class: 'bars' }) : null,
    h('div', { class: 'overlay', id: 'overlay' }),
    h('div', { class: 'mon-meta', id: 'monMeta' }, sh ? `${sh.id} · ${sh.section}` : ''),
    sh && sh.source !== 'procedural' ? h('div', { class: 'mon-badge' }, (sh.source === 'claude' ? 'Claude-drawn ' : 'AI ') + ((P().shot_files[sh.id] || {}).clip ? 'clip' : (P().shot_files[sh.id] || {}).key ? 'still' : '(not made yet)')) : null);
}

// ---------------- stage panes ----------------
const panes = {};

panes.song = () => {
  const d = P();
  const server = JSON.stringify(d.song);
  let conflict = false;
  if (S.songDraft && server !== S.songBase) {
    // The song changed on the server (Claude wrote or revised it). Take it unless you have unsaved edits.
    if (JSON.stringify(S.songDraft) === S.songBase) S.songDraft = null; else conflict = true;
  }
  if (!S.songDraft) { S.songDraft = clone(d.song || { title: d.slug, style: '', style_terse: '', exclude: '', lyrics: '', settings: { model: 'v6', variety: 0, style_influence: 70, weirdness: 30, max_mode: false, vocal_gender: null } }); S.songBase = server; }
  const D = S.songDraft; const dirty = JSON.stringify(D) !== server;
  const writing = busy('song');
  const count = (txt, n) => h('span', { class: `count ${txt.length > n * 0.9 ? 'warn' : ''}` }, `${txt.length} / ${n}`);
  const ta = (k, cls, n) => {
    const t = h('textarea', { class: cls, spellcheck: k === 'lyrics' ? 'true' : 'false', 'aria-label': k });
    t.value = D[k] || '';
    t.addEventListener('input', () => { D[k] = t.value; t.parentElement.querySelector('.count').replaceWith(count(t.value, n)); $('#saveSong').disabled = false; });
    return t;
  };
  const st = D.settings;
  const setting = (label, el) => h('div', { class: 's' }, h('span', { class: 'label' }, label), el);
  const num = k => { const i = h('input', { type: 'number', min: 0, max: 100, value: st[k], 'aria-label': k }); i.addEventListener('input', () => { st[k] = +i.value; $('#saveSong').disabled = false; }); return i; };
  const sel = (k, opts) => { const s = h('select', { 'aria-label': k }, ...opts.map(([v, l]) => h('option', { value: v, selected: String(st[k]) === String(v) }, l))); s.addEventListener('change', () => { st[k] = s.value === 'true' ? true : s.value === 'false' ? false : s.value === '' ? null : s.value; $('#saveSong').disabled = false; }); return s; };
  const title = h('input', { class: 'title', value: D.title, 'aria-label': 'Title' }); title.addEventListener('input', () => { D.title = title.value; $('#saveSong').disabled = false; });
  return [head('Song sheet',
      h('button', { class: 'btn ghost', onclick: openImport }, 'Import from a chat'),
      h('button', { class: 'btn primary', id: 'saveSong', disabled: !dirty || writing, onclick: saveSong }, 'Save')),
    h('div', { class: 'pane-b sheet' },
      writing ? h('div', { class: 'sheet-confirm' }, h('span', {}, 'Claude is writing the song. It will appear here when the job finishes.')) : null,
      conflict ? h('div', { class: 'sheet-confirm' }, h('span', {}, 'A new version arrived from Claude, but you have unsaved edits here.'),
        h('div', { class: 'wrap-gap' }, h('button', { class: 'btn primary', onclick: () => { S.songDraft = null; renderStage(); } }, "Load Claude's version"),
          h('button', { class: 'btn', onclick: () => { S.songBase = server; renderStage(); } }, 'Keep my edits'))) : null,
      title,
      ...[['style', 'Style', 'mono-ta', 1000], ['style_terse', 'Style (terse fallback)', 'mono-ta', 1000], ['exclude', 'Exclude styles', 'mono-ta', 1000], ['lyrics', 'Lyrics', 'lyrics', 5000]]
        .map(([k, label, cls, n]) => h('div', { class: 'block' }, h('div', { class: 'row' }, h('span', { class: 'label' }, label), count(D[k] || '', n), h('button', { class: 'btn ghost', onclick: () => copyText(D[k] || '') }, 'Copy for Suno')), ta(k, cls, n))),
      h('div', { class: 'block' }, h('span', { class: 'label' }, 'Suno settings'),
        h('div', { class: 'settings' },
          setting('Model', sel('model', [['v6', 'v6'], ['v6-wild', 'v6-wild'], ['v6-mini', 'v6-mini']])),
          setting('Variety', num('variety')), setting('Style influence', num('style_influence')), setting('Weirdness', num('weirdness')),
          setting('Max Mode', sel('max_mode', [['false', 'off'], ['true', 'on']])),
          setting('Vocal', sel('vocal_gender', [['', 'any'], ['male', 'male'], ['female', 'female']])))),
      d.song?.notes ? h('div', { class: 'block' }, h('span', { class: 'label' }, 'Notes from the songwriter'), h('p', { class: 'hint', style: 'margin:0;white-space:pre-wrap' }, d.song.notes)) : null)];
};
async function saveSong() {
  try { await api('PUT', `/api/projects/${S.slug}/song`, S.songDraft); toast('Song saved.'); S.songDraft = null; await refresh(true); }
  catch (e) { toast(e.message, true); }
}
function openImport() {
  const ta = h('textarea', { placeholder: 'Paste the whole reply from your Claude chat (with the Style, Exclude and Lyrics code blocks).' });
  const back = h('div', { class: 'modal-back', onclick: e => { if (e.target === back) back.remove(); } },
    h('div', { class: 'modal', role: 'dialog', 'aria-label': 'Import song' }, h('b', { class: 'serif', style: 'font-size:20px' }, 'Import from a chat'), ta,
      h('div', { class: 'wrap-gap' }, h('button', { class: 'btn primary', onclick: async () => {
        try { await api('POST', `/api/projects/${S.slug}/song/import`, { markdown: ta.value }); back.remove(); S.songDraft = null; toast('Imported.'); refresh(true); } catch (e) { toast(e.message, true); }
      } }, 'Import'), h('button', { class: 'btn ghost', onclick: () => back.remove() }, 'Cancel'))));
  document.body.append(back); ta.focus();
}

panes.suno = () => {
  const d = P(); const clips = d.suno?.clips || [];
  const urlIn = h('input', { class: 'text', placeholder: 'Suno song URL (optional, for your records)', 'aria-label': 'Suno URL' });
  const alIn = h('input', { type: 'file', accept: '.lrc,.srt,.json', 'aria-label': 'Timed lyrics file' });
  const upload = async f => {
    const fd = new FormData(); fd.append('file', f); if (alIn.files[0]) fd.append('aligned', alIn.files[0]); fd.append('url', urlIn.value);
    toast(`Uploading ${f.name}...`);
    try { await api('POST', `/api/projects/${S.slug}/audio`, fd, true); toast('Imported. Next: align the lyrics.'); await refresh(true); } catch (e) { toast(e.message, true); }
  };
  const fileIn = h('input', { type: 'file', id: 'file', accept: 'audio/*', hidden: true, onchange: e => e.target.files[0] && upload(e.target.files[0]) });
  const drop = h('label', { class: 'drop', for: 'file' }, h('strong', {}, d.audio ? 'Drop a new take to replace it' : 'Drop your keeper here'),
    h('span', {}, 'The MP3 you downloaded from suno.com. Audition in Suno first; download only the keeper.'), fileIn);
  drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); const f = e.dataTransfer.files[0]; if (f) upload(f); });
  const song = d.song;
  return [head('Suno', d.audio ? h('span', { class: 'mono', style: 'font-size:12px;color:var(--muted)' }, `Using ${d.audio}`) : null),
    h('div', { class: 'pane-b', style: 'display:grid;gap:14px' },
      song ? h('div', { class: 'wrap-gap' }, h('span', { class: 'label' }, 'Paste into Suno'),
        h('button', { class: 'btn', onclick: () => copyText(song.style) }, 'Copy style'), h('button', { class: 'btn', onclick: () => copyText(song.exclude) }, 'Copy exclude'),
        h('button', { class: 'btn', onclick: () => copyText(song.lyrics) }, 'Copy lyrics'),
        h('span', { class: 'hint' }, `${song.settings.model}, Variety ${song.settings.variety}, Style Influence ${song.settings.style_influence}, Weirdness ${song.settings.weirdness}`)) : null,
      drop,
      h('div', { class: 'wrap-gap' }, urlIn),
      h('label', { class: 'hint wrap-gap' }, 'Optional, and better than auto-detection: timed lyrics for this take (.lrc or .srt from Suno Lyric Downloader)', alIn),
      d.audio ? h('audio', { controls: true, src: fileUrl(d.audio), style: 'width:100%' }) : null,
      clips.length > 1 ? h('div', { class: 'takes' }, ...clips.map(c => h('div', { class: `take ${d.suno.chosen === c.id ? 'chosen' : ''}` },
        h('div', { class: 'row' }, h('span', { class: 'label' }, c.local_path || c.id), h('span', { class: 'mono hint' }, c.duration ? fmt(c.duration) : '')),
        c.local_path ? h('audio', { controls: true, src: fileUrl(c.local_path), style: 'width:100%' }) : null,
        h('div', { class: 'row' }, d.suno.chosen === c.id ? h('span', { style: 'color:var(--ok);font-size:12.5px' }, 'In use')
          : h('button', { class: 'btn', onclick: async () => { try { await api('POST', `/api/projects/${S.slug}/pick`, { clip_id: c.id }); refresh(true); } catch (e) { toast(e.message, true); } } }, 'Use this take'))))) : null,
      h('div', { class: 'wrap-gap' },
        keySet('SUNOAPI_KEY') ? h('button', { class: 'btn', onclick: () => run('suno_api') }, 'Generate with the Suno API') :
          h('span', { class: 'hint' }, 'Automatic generation needs a Suno API provider key (optional). ', h('a', { href: '#/settings' }, 'API keys')),
        d.audio ? h('button', { class: 'btn primary', onclick: () => { run('align'); go('timing'); } }, 'Next: align lyrics') : null))];
};

panes.timing = () => {
  const d = P();
  const method = h('select', { 'aria-label': 'Alignment method', style: 'width:auto' }, ...[['auto', 'Best available'], ['suno', "Suno's timing"], ['whisper', 'Whisper'], ['even', 'Quick guess']].map(([v, l]) => h('option', { value: v }, l)));
  const tlIn = h('input', { type: 'file', accept: '.lrc,.srt,.json', multiple: true, hidden: true, id: 'tlIn', onchange: e => importTimed(e.target.files) });
  const actions = h('div', { class: 'wrap-gap' }, method, h('button', { class: 'btn primary', disabled: !d.audio || busy('align'), onclick: () => run('align', { method: method.value }) }, busy('align') ? 'Aligning...' : d.timing ? 'Re-align' : 'Align lyrics'),
    h('label', { for: 'tlIn', class: 'btn', style: 'cursor:pointer' }, 'Import .lrc / .srt'), tlIn,
    h('span', { class: 'hint' }, 'From the Suno Lyric Downloader extension. Uses Suno\'s own timing.'));
  if (!d.timing) return [head('Timing'), h('div', { class: 'pane-b' }, place('No timing yet', d.audio ? 'Align the lyrics to the audio. Uses Suno\'s own timing if you added it, else Whisper, else a quick guess.' : 'Import the audio in the Suno step first.', actions))];
  const list = h('div', { class: 'karaoke', id: 'karaoke' });
  let prev = null;
  lines().forEach((ln, li) => {
    if (ln.section !== prev) { list.append(h('div', { class: 'kl' }, h('span', { class: 'sec-l' }, ln.section))); prev = ln.section; }
    list.append(h('div', { class: 'kl', 'data-li': li }, h('span', { class: 'tm' }, fmt(ln.start)),
      h('span', { class: 'ws' }, ln.words.map(wi => { const w = words()[wi]; return [h('button', {
        class: `${w.confident ? '' : 'low'} ${S.pickWord === wi ? 'pick' : ''}`, 'data-w': wi,
        onclick: () => { S.pickWord = wi; seek(w.start - 0.4); renderInspector(); markPick(); } }, w.text), ' ']; }))));
  });
  const low = words().filter(w => !w.confident).length;
  const tap = S.tap;
  const tapBox = h('div', { class: `tap ${tap ? 'on' : ''}` },
    h('div', { class: 'wrap-gap' }, h('b', { style: 'font-weight:500' }, 'Tap to sync'),
      h('button', { class: 'btn', style: 'margin-left:auto', onclick: () => { if (S.tap) { S.tap = null; setPlay(false); } else { S.tap = { i: 0, taps: [] }; seek(0); setPlay(true); } renderStage(); } }, tap ? 'Cancel' : 'Start')),
    tap ? h('span', { class: 'hint' }, 'Listening. Press ', h('kbd', {}, 'Space'), ` when line ${tap.i + 1} of ${lines().length} starts: "${lines()[Math.min(tap.i, lines().length - 1)].text}"`)
      : h('span', { class: 'hint' }, 'When automatic alignment fumbles, play the song and tap each line start. Words are spread inside each line.'));
  return [head('Timing check', h('span', { class: 'hint' }, `Source: ${d.timing.source}${low ? ` · ${low} words to check (wavy underline)` : ''}`)),
    h('div', { class: 'pane-b' }, actions, h('div', { style: 'height:10px' }), list, tapBox)];
};
async function importTimed(files) {
  if (!files || !files.length) return;
  const fd = new FormData(); for (const f of files) fd.append('files', f);
  try {
    const r = await api('POST', `/api/projects/${S.slug}/timed-lyrics`, fd, true);
    toast(r.job ? `Imported ${r.saved.join(', ')}. Re-aligning with it now.` : `Imported ${r.saved.join(', ')}. Add the audio, then align.`);
    await refresh(true);
  } catch (e) { toast(e.message, true); }
}
function markPick() { document.querySelectorAll('#karaoke button[data-w]').forEach(b => b.classList.toggle('pick', +b.dataset.w === S.pickWord)); }
async function saveTiming(tm, note) {
  try { await api('PUT', `/api/projects/${S.slug}/timing`, { timing: tm, note }); await refresh(); } catch (e) { toast(e.message, true); }
}
function finishTap() {
  const tm = clone(P().timing); const taps = S.tap.taps; S.tap = null;
  tm.lines.forEach((ln, li) => {
    const start = taps[li]; if (start == null) return;
    const nextStart = li + 1 < taps.length ? taps[li + 1] : Math.min(tm.duration, start + (ln.end - ln.start) * 1.3);
    const oldLen = Math.max(0.2, ln.end - ln.start); const newLen = Math.max(0.3, Math.min(oldLen, nextStart - start - 0.15));
    ln.words.forEach(wi => { const w = tm.words[wi]; const a = (w.start - ln.start) / oldLen, b = (w.end - ln.start) / oldLen; w.start = +(start + a * newLen).toFixed(3); w.end = +(start + b * newLen).toFixed(3); w.confident = true; });
  });
  saveTiming(tm, `Tap-synced ${taps.length} lines`);
  toast('Timing saved from your taps.');
}

const ART_PRESETS = ['simple animation', 'continuous line drawing', 'watercolor', 'ink and wash', 'paper cut-out',
  'risograph print', 'chalk on blackboard', 'charcoal sketch', 'geometric minimal', 'neon line art'];
const MODES = [['claude', 'Claude-drawn'], ['internal', 'Procedural'], ['hybrid', 'Hybrid'], ['external', 'AI video']];
const MODE_HINT = {
  claude: 'Claude draws and animates every shot in your chosen style. Only the Claude calls cost money; rendering is local.',
  internal: 'Abstract audio-reactive scenes (nebula, smoke, light rays). Free and instant.',
  hybrid: 'AI video for the key moments, procedural everywhere else.',
  external: 'AI video for every shot via fal.ai.',
};
function artEditor(art, onChange) {
  art = art || { preset: '', vibe: '', avoid: '' };
  const vibe = h('textarea', { 'aria-label': 'Vibe', placeholder: 'Describe the vibe and style cues, e.g. loose ink on cream paper, morning light, gentle humour, hand-made wobble' });
  vibe.value = art.vibe || ''; vibe.addEventListener('change', () => { art.vibe = vibe.value; onChange(art, false); });
  const avoid = h('input', { class: 'text', 'aria-label': 'Avoid', placeholder: 'Avoid, e.g. neon, glossy 3D, clip-art faces, heavy outlines', value: art.avoid || '' });
  avoid.addEventListener('change', () => { art.avoid = avoid.value; onChange(art, false); });
  return h('div', { style: 'display:grid;gap:8px' },
    h('span', { class: 'label' }, 'Drawing style'),
    h('div', { class: 'chips', style: 'padding:0' }, ...ART_PRESETS.map(pr => h('button', { class: 'chip', style: art.preset === pr ? 'color:var(--fg);border-color:var(--accent)' : '',
      onclick: () => { art.preset = art.preset === pr ? '' : pr; onChange(art, true); } }, pr))),
    vibe, avoid);
}

panes.board = () => {
  const d = P(); const b = d.storyboard;
  if (!S.artDraft) S.artDraft = clone(b?.art || { preset: 'watercolor', vibe: '', avoid: '' });
  const dir = h('input', { class: 'text', placeholder: 'Direction for Claude, e.g. memory and light; warm amber vs cold blue; no people', 'aria-label': 'Direction' });
  const mode = h('div', { class: 'segc' }, ...MODES.map(([m, l]) => h('button', { 'aria-pressed': String(S.boardMode === m), onclick: () => { S.boardMode = m; renderStage(); } }, l)));
  const claude = S.boardMode === 'claude';
  const gen = h('div', { style: 'display:grid;gap:10px' },
    h('div', { class: 'wrap-gap' }, h('span', { class: 'label' }, b ? 'New storyboard' : 'Storyboard'), mode),
    h('span', { class: 'hint' }, MODE_HINT[S.boardMode]),
    claude ? artEditor(S.artDraft, (a, rerender) => { S.artDraft = a; if (rerender) renderStage(); }) : null,
    dir,
    h('div', { class: 'wrap-gap' },
      h('button', { class: 'btn primary', disabled: !d.analysis || busy('storyboard') || !keySet('ANTHROPIC_API_KEY'), onclick: () => run('storyboard', { mode: S.boardMode, direction: dir.value, art: claude ? S.artDraft : null }) }, keySet('ANTHROPIC_API_KEY') ? 'Direct with Claude' : 'Direct with Claude (needs key)'),
      h('button', { class: 'btn', disabled: !d.analysis || busy('storyboard'), onclick: () => run('storyboard', { mode: S.boardMode, heuristic: true, art: claude ? S.artDraft : null }) }, 'Quick storyboard (no AI)'),
      b ? h('span', { class: 'hint' }, 'Replaces the current shot list and clears approvals.') : null));
  if (!b) return [head('Board'), h('div', { class: 'pane-b' }, d.analysis ? gen : place('Not yet', 'Align the lyrics first; the director needs line timings and bar lines.', h('button', { class: 'btn', onclick: () => go('timing') }, 'Open Timing')))];
  const drawn = b.shots.filter(s => s.source === 'claude').length;
  return [head('Monitor', h('span', { class: 'mono hint' }, 'animatic: stills + audio')),
    h('div', { class: 'pane-b', style: 'display:grid;gap:12px' }, monitor(),
      h('div', { class: 'wrap-gap' }, h('span', { class: 'label' }, 'Lyrics on screen'),
        h('div', { class: 'segc' }, ...[['auto', 'Auto'], ['words', 'Word by word'], ['lines', 'Whole lines']].map(([v, l]) =>
          h('button', { 'aria-pressed': String((b.lyric_style || 'auto') === v), onclick: () => { b.lyric_style = v; curLine = -2; putBoard(`Lyrics on screen: ${l.toLowerCase()}`); renderStage(); } }, l))),
        h('span', { class: 'hint' }, (b.lyric_style || 'auto') === 'auto' ? `Auto is using ${lyricStyle() === 'lines' ? 'whole lines (timing is line-level)' : 'word by word'}.` : '')),
      b.concept ? h('div', { class: 'hint' }, h('span', { class: 'serif', style: 'color:var(--fg);font-size:15px' }, 'Concept. '), b.concept) : null,
      drawn ? h('details', { open: S.open.style, ontoggle: e => { S.open.style = e.currentTarget.open; } }, h('summary', { class: 'hint', style: 'cursor:pointer' }, `Drawing style (${drawn} Claude-drawn shot${drawn > 1 ? 's' : ''}): ${b.art?.preset || 'custom'}`),
        h('div', { style: 'margin-top:10px;display:grid;gap:8px' },
          artEditor(clone(b.art || {}), (a) => { b.art = a; putBoard('Changed the drawing style'); }),
          h('div', { class: 'wrap-gap' }, h('button', { class: 'btn', disabled: busy('draw') || !keySet('ANTHROPIC_API_KEY'), onclick: () => run('draw', { redo: true, restyle: true }) }, 'Redraw every scene in this style'),
            h('span', { class: 'hint' }, 'Writes a new style kit, then redraws all Claude-drawn shots.')))) : null,
      h('details', { open: S.open.regen, ontoggle: e => { S.open.regen = e.currentTarget.open; } }, h('summary', { class: 'hint', style: 'cursor:pointer' }, 'Regenerate the storyboard'), h('div', { style: 'margin-top:10px' }, gen)))];
};

const isArt = sh => sh.source === 'claude';
const needsStill = sh => sh.source === 'generated' || sh.source === 'claude';

panes.look = () => {
  const d = P(); const b = d.storyboard;
  if (!b) return [head('Look'), h('div', { class: 'pane-b' }, place('No storyboard yet', 'Stills come from the shot list.', h('button', { class: 'btn', onclick: () => go('board') }, 'Open Board')))];
  const noKey = b.shots.filter(s => needsStill(s) && !(d.shot_files[s.id] || {}).key);
  const noKeyGen = noKey.filter(s => s.source === 'generated'), noKeyArt = noKey.filter(isArt);
  const grid = h('div', { class: 'grid-look' }, ...b.shots.map(sh => {
    const f = d.shot_files[sh.id] || {}; const a = d.approvals[sh.id];
    const pill = !needsStill(sh) ? h('span', { class: 'pill' }, sh.scene) : !f.key ? h('span', { class: 'pill need' }, isArt(sh) ? 'Not drawn yet' : 'Needs AI still')
      : a?.approved ? h('span', { class: 'pill ok' }, 'Approved') : a && a.note ? h('span', { class: 'pill no' }, 'Redo') : h('span', { class: 'pill need' }, 'Needs approval');
    const img = shotImg(sh);
    return h('button', { class: `card ${S.sel === sh.id ? 'sel' : ''}`, onclick: () => select(sh.id) },
      h('div', { class: 'im', style: img ? `background-image:url(${img})` : 'background:var(--panel)' }, pill),
      h('div', { class: 'cap' }, h('b', {}, `${sh.id} · ${fmt(sh.start)}${isArt(sh) ? ' · Claude-drawn' : ''}`), h('span', {}, sh.description || sh.scene)));
  }));
  return [head('Look', h('span', { class: 'hint' }, h('kbd', {}, 'A'), ' approve  ', h('kbd', {}, 'R'), ' redo  ', h('kbd', {}, '←'), h('kbd', {}, '→'), ' move')),
    h('div', { class: 'pane-b', style: 'display:grid;gap:12px' },
      h('div', { class: 'wrap-gap' }, h('button', { class: 'btn', disabled: busy('stills'), onclick: () => run('stills') }, busy('stills') ? 'Rendering stills...' : 'Refresh stills'),
        noKeyArt.length ? h('button', { class: 'btn primary', disabled: !keySet('ANTHROPIC_API_KEY') || busy('draw'), onclick: () => run('draw', { shots: noKeyArt.map(s => s.id) }) }, busy('draw') ? 'Claude is drawing...' : `Draw ${noKeyArt.length} scene${noKeyArt.length > 1 ? 's' : ''} with Claude`) : null,
        noKeyGen.length ? h('button', { class: 'btn primary', disabled: !keySet('FAL_KEY') || busy('keyframes'), onclick: () => run('keyframes', { shots: noKeyGen.map(s => s.id) }) }, `Generate ${noKeyGen.length} AI still${noKeyGen.length > 1 ? 's' : ''}`) : null,
        noKeyGen.length && !keySet('FAL_KEY') ? h('span', { class: 'hint' }, 'AI stills need a fal.ai key. ', h('a', { href: '#/settings' }, 'API keys')) : null,
        h('a', { href: fileUrl('stills/contact_sheet.jpg'), target: '_blank', class: 'hint' }, 'Contact sheet')),
      grid)];
};

panes.picture = () => {
  const d = P(); const b = d.storyboard; const shots = b?.shots || [];
  const gen = shots.filter(s => s.source === 'generated'), art = shots.filter(isArt);
  if (!gen.length && !art.length) return [head('Picture'), h('div', { class: 'pane-b' }, place('Nothing to generate', 'This storyboard is all procedural. Set a shot\'s picture source to Claude-drawn or AI video to use them.', h('button', { class: 'btn', onclick: () => go('render') }, 'Open Render')))];
  const durs = [...(d.config.video_durations || [5, 10])].sort((a, b) => a - b), stretch = d.config.max_stretch || 1.3;
  const durOf = sh => durs.find(x => x * stretch >= sh.end - sh.start) || durs[durs.length - 1];
  const ready = gen.filter(s => d.approvals[s.id]?.approved && !(d.shot_files[s.id] || {}).clip);
  const readyArt = art.filter(s => d.approvals[s.id]?.approved && !(d.shot_files[s.id] || {}).clip);
  const secs = ready.reduce((a, s) => a + durOf(s), 0); const rate = d.config.rate_per_second;
  const running = busy('animate');
  const artBox = art.length ? h('div', { class: 'sheet-confirm', style: 'border-color:var(--rule);background:var(--panel)' }, h('span', { class: 'label' }, 'Claude-drawn scenes'),
    readyArt.length ? h('div', { class: 'wrap-gap' }, h('button', { class: 'btn primary', disabled: busy('draw_render'), onclick: () => run('draw_render', { shots: readyArt.map(s => s.id) }) }, busy('draw_render') ? 'Rendering scenes...' : `Render ${readyArt.length} approved scene${readyArt.length > 1 ? 's' : ''}`),
        h('span', { class: 'hint' }, 'Drawn frame by frame on this computer. Free; takes about as long as the clips play.'))
      : h('p', { class: 'empty' }, art.every(s => (d.shot_files[s.id] || {}).clip) ? 'Every Claude-drawn shot has a clip.' : 'Approve drawn stills in Look first.')) : null;
  const confirm = !gen.length ? null : ready.length && !running ? h('div', { class: 'sheet-confirm' }, h('span', { class: 'label' }, 'Before you spend'),
    h('div', { class: 'wrap-gap', style: 'gap:18px' }, h('span', { class: 'money' }, `~$${(secs * rate).toFixed(2)}`),
      h('span', { class: 'hint' }, `${ready.length} clip${ready.length > 1 ? 's' : ''}, ${secs} s of video on ${d.config.video_model}. Estimate at $${rate.toFixed(2)}/s (set ui.video_rate_per_second in songvid.yaml); fal.ai bills the real rate.`)),
    h('div', { class: 'wrap-gap' }, h('button', { class: 'btn primary', disabled: !keySet('FAL_KEY'), onclick: e => { e.currentTarget.disabled = true; run('animate', { confirm: true, shots: ready.map(s => s.id) }); } }, `Animate ${ready.length} shot${ready.length > 1 ? 's' : ''}`),
      !keySet('FAL_KEY') ? h('span', { class: 'hint' }, 'Needs a fal.ai key. ', h('a', { href: '#/settings' }, 'API keys')) : null))
    : h('p', { class: 'empty' }, running ? 'Clips are generating. Progress is in Jobs.' : gen.every(s => (d.shot_files[s.id] || {}).clip) ? 'Every AI shot has a clip.' : 'No AI video ready. Approve AI stills in Look first.');
  const rows = [...art, ...gen].sort((a, b) => a.start - b.start).map(sh => { const f = d.shot_files[sh.id] || {}; const a = d.approvals[sh.id];
    const st = f.clip ? h('span', { class: 'qstate ok' }, 'Clip ready') : a?.approved ? h('span', { class: 'qstate' }, 'Ready') : f.key ? h('span', { class: 'qstate' }, 'Approve the still first') : h('span', { class: 'qstate' }, isArt(sh) ? 'Not drawn yet' : 'No still yet');
    return h('div', { class: 'qrow' }, f.clip ? h('video', { src: fileUrl(f.clip), poster: shotImg(sh) || false, preload: 'metadata', muted: true, loop: true, playsinline: true, class: 'im', style: 'width:100%;object-fit:cover', onmouseenter: e => e.target.play(), onmouseleave: e => e.target.pause() })
        : h('div', { class: 'im', style: shotImg(sh) ? `background-image:url(${shotImg(sh)})` : 'background:var(--panel-2)' }),
      h('div', { class: 't' }, h('b', {}, `${sh.id} · ${sh.section} · ${(sh.end - sh.start).toFixed(1)} s · ${isArt(sh) ? 'Claude-drawn' : 'AI video'}`), h('span', {}, sh.motion_prompt || sh.description || '')), st); });
  return [head('Picture'), h('div', { class: 'pane-b', style: 'display:grid;gap:14px' }, artBox, confirm, h('div', { class: 'queue' }, ...rows))];
};

panes.render = () => {
  const d = P();
  if (!d.storyboard) return [head('Render'), h('div', { class: 'pane-b' }, place('Nothing to render yet', 'Make a storyboard first.', h('button', { class: 'btn', onclick: () => go('board') }, 'Open Board')))];
  const sl = S.slice; const D = dur();
  const a = sl ? sl[0] : Math.min(30, Math.max(0, D - 30)), b = sl ? sl[1] : Math.min(D, a + 30);
  const sel = d.renders.find(r => r.file === S.renderSel) || d.renders[0];
  return [head('Render'), h('div', { class: 'pane-b', style: 'display:grid;gap:14px' },
    sel ? h('video', { class: 'player', controls: true, src: fileUrl(sel.file), key: sel.file }) : monitor(),
    h('div', { class: 'wrap-gap' },
      h('button', { class: 'btn primary', onclick: () => run('render', { preview: true, start: a, end: b }) }, `Preview ${fmt(a)} to ${fmt(b)}`),
      h('button', { class: 'btn', onclick: () => run('render', { preview: true, start: 0, end: D }) }, 'Preview full song'),
      h('button', { class: 'btn', onclick: () => run('render', { preview: false, start: 0, end: D }) }, 'Final render, 1080p'),
      h('span', { class: 'hint' }, sl ? 'Slice from the timeline.' : 'Drag across the timeline ruler to choose a slice.')),
    d.renders.length ? h('div', { class: 'renders' }, ...d.renders.map(r => h('div', { class: 'rrow', style: r === sel ? 'border-color:var(--faint)' : '' },
      h('div', {}, h('b', {}, r.name), h('br'), h('span', {}, `${r.duration ? r.duration.toFixed(1) + ' s · ' : ''}${r.size_mb} MB · ${new Date(r.mtime * 1000).toLocaleString()}`)),
      h('a', { href: fileUrl(r.file), download: '', class: 'btn ghost', style: 'text-decoration:none;color:inherit' }, 'Download'),
      h('button', { class: 'btn', onclick: () => { S.renderSel = r.file; renderStage(); } }, 'Watch')))) : h('p', { class: 'empty' }, 'No renders yet. Start with a 30 s preview.'))];
};

function renderStage() {
  const p = $('#stagePane'); p.innerHTML = '';
  p.append(...panes[S.stage]()); curShot = null; curLine = -2; updateFrame();
}

// ---------------- inspector ----------------
function renderInspector() {
  const el = $('#inspector'); el.innerHTML = ''; const d = P();
  if (S.stage === 'song') {
    const brief = h('textarea', { 'aria-label': 'Brief', placeholder: 'Who is it for, what happened, 3 to 5 concrete details, the mood.' }); brief.value = d.brief || '';
    brief.addEventListener('change', () => api('PUT', `/api/projects/${S.slug}/brief`, { brief: brief.value }).catch(e => toast(e.message, true)));
    const rev = h('input', { class: 'text', placeholder: 'Feedback, e.g. chorus is too wordy', 'aria-label': 'Revision feedback' });
    const key = keySet('ANTHROPIC_API_KEY');
    el.append(head('Brief'), h('div', { class: 'pane-b', style: 'display:grid;gap:10px' }, brief,
      h('button', { class: 'btn primary', disabled: !key || busy('song'), onclick: () => run('song', { brief: brief.value }) }, busy('song') ? 'Writing...' : 'Write with Claude'),
      h('span', { class: 'hint' }, key ? 'Uses your Suno v6 songwriting skill when it is installed.' : h('span', {}, 'Needs an Anthropic key. ', h('a', { href: '#/settings' }, 'API keys'), '. Or write in a Claude chat and use Import.')),
      d.song?.lyrics?.length > 40 ? h('div', { style: 'display:grid;gap:6px' }, h('span', { class: 'label' }, 'Revise one thing'), rev,
        h('button', { class: 'btn', disabled: !key || busy('song'), onclick: () => { if (!rev.value.trim()) return toast('Say what to change.', true); run('song', { revise: rev.value }); } }, 'Revise')) : null));
    return;
  }
  if (S.stage === 'timing' && S.pickWord != null && d.timing) {
    const w = words()[S.pickWord]; const ln = lines()[w.line];
    const nudge = delta => { const tm = clone(d.timing); const x = tm.words[S.pickWord]; x.start = +(x.start + delta).toFixed(3); x.end = +(x.end + delta).toFixed(3); x.confident = true; saveTiming(tm, `Nudged "${x.text}" ${delta > 0 ? '+' : ''}${Math.round(delta * 1000)} ms`); };
    el.append(head('Word'), h('div', { class: 'pane-b' }, h('div', { class: 'serif', style: 'font-size:30px;line-height:1.1;margin-bottom:8px' }, w.text),
      h('dl', { class: 'kv' }, h('dt', {}, 'Line'), h('dd', {}, ln.text), h('dt', {}, 'Start'), h('dd', { class: 'mono' }, fmt(w.start)), h('dt', {}, 'End'), h('dd', { class: 'mono' }, fmt(w.end)),
        h('dt', {}, 'Confidence'), h('dd', { style: `color:${w.confident ? 'var(--ok)' : 'var(--accent)'}` }, w.confident ? 'Matched' : 'Interpolated, worth a listen')),
      h('div', { class: 'wrap-gap', style: 'margin-top:12px' }, ...[-0.1, -0.05, 0.05, 0.1].map(x => h('button', { class: 'btn', onclick: () => nudge(x) }, `${x > 0 ? '+' : ''}${Math.round(x * 1000)} ms`)),
        h('button', { class: 'btn', onclick: () => { seek(w.start - 0.8); setPlay(true); } }, 'Hear it'))));
    return;
  }
  const b = d.storyboard; const sh = b && (b.shots.find(s => s.id === S.sel) || b.shots[0]);
  if (!sh) { el.append(head('Inspector'), h('div', { class: 'pane-b hint' }, 'Select a shot on the timeline to edit it.')); return; }
  const save = (note) => putBoard(note);
  const set = (k, v, label) => { sh[k] = v; save(`${sh.id}: ${label || k} changed`); };
  const seg = (k, opts) => h('div', { class: 'segc' }, ...opts.map(o => h('button', { 'aria-pressed': String(sh[k] === o), onclick: () => { set(k, o); } }, o)));
  const range = (k, label) => h('div', { class: 'field' }, h('div', { class: 'row' }, h('span', { class: 'label' }, label), h('span', { class: 'mono', style: 'margin-left:auto;font-size:12px', id: `v-${k}` }, sh[k].toFixed(2))),
    h('input', { type: 'range', min: 0, max: 1, step: 0.01, value: sh[k], 'aria-label': label, oninput: e => { sh[k] = +e.target.value; $(`#v-${k}`).textContent = sh[k].toFixed(2); }, onchange: () => set(k, sh[k], label.toLowerCase()) }));
  const bars = P().analysis ? Math.round((sh.end - sh.start) / (240 / P().analysis.tempo)) : null;
  const desc = h('input', { class: 'text serif', style: 'font-size:16px', value: sh.description, 'aria-label': 'Description' }); desc.addEventListener('change', () => set('description', desc.value, 'description'));
  const fields = [h('div', { class: 'field' }, desc, h('span', { class: 'mono hint' }, `${fmt(sh.start)} to ${fmt(sh.end)} · ${(sh.end - sh.start).toFixed(1)} s${bars ? ` · ${bars} bars` : ''}`)),
    h('div', { class: 'field' }, h('span', { class: 'label' }, 'Picture source'), h('div', { class: 'segc' }, ...[['procedural', 'Procedural'], ['claude', 'Claude-drawn'], ['generated', 'AI video']].map(([v, l]) => h('button', { 'aria-pressed': String(sh.source === v), onclick: () => set('source', v, 'picture source') }, l))))];
  if (sh.source !== 'procedural') {
    const f = d.shot_files[sh.id] || {}; const a = d.approvals[sh.id];
    const ta1 = h('textarea', { 'aria-label': 'Image prompt' }); ta1.value = sh.image_prompt || ''; ta1.addEventListener('change', () => set('image_prompt', ta1.value, 'image prompt'));
    const ta2 = h('textarea', { 'aria-label': 'Motion prompt' }); ta2.value = sh.motion_prompt || ''; ta2.addEventListener('change', () => set('motion_prompt', ta2.value, 'motion prompt'));
    const note = h('input', { class: 'text', placeholder: 'What to change on redo (optional)', 'aria-label': 'Redo note' });
    const art = sh.source === 'claude';
    const code = art ? h('details', {}, h('summary', { class: 'hint', style: 'cursor:pointer' }, 'View the code Claude wrote'),
      h('pre', { class: 'mono', style: 'font-size:11px;max-height:260px;overflow:auto;white-space:pre-wrap;background:var(--panel-2);padding:8px;border-radius:6px', id: 'codeView' }, 'Loading...')) : null;
    if (code) code.addEventListener('toggle', async () => { if (!code.open) return; try { const r = await fetch(fileUrl(`art/${sh.id}.js`)); $('#codeView').textContent = r.ok ? await r.text() : 'Not drawn yet.'; } catch { } });
    fields.push(h('div', { class: 'field' }, h('span', { class: 'label' }, art ? 'Claude-drawn still' : 'AI still'),
      f.key ? h('div', { class: 'big', style: `background-image:url(${fileUrl(f.key)})` }) : h('p', { class: 'hint', style: 'margin:0' }, art ? 'Not drawn yet.' : 'Not generated yet.'),
      f.key ? h('div', { class: 'row' }, h('button', { class: 'btn primary', onclick: () => approve(sh.id, true) }, a?.approved ? 'Approved' : 'Approve'),
        h('button', { class: 'btn', onclick: () => approve(sh.id, false, note.value) }, 'Redo'), h('span', { class: 'hint' }, a?.approved ? (art ? 'Ready to render' : 'Ready to animate') : 'Waiting on you')) :
        art ? h('button', { class: 'btn', disabled: !keySet('ANTHROPIC_API_KEY') || busy('draw'), onclick: () => run('draw', { shots: [sh.id] }) }, 'Draw this scene')
          : h('button', { class: 'btn', disabled: !keySet('FAL_KEY'), onclick: () => run('keyframes', { shots: [sh.id] }) }, 'Generate this still'),
      f.key ? note : null, code),
      h('div', { class: 'field' }, h('span', { class: 'label' }, art ? 'Drawing brief' : 'Image prompt'), ta1), h('div', { class: 'field' }, h('span', { class: 'label' }, art ? 'Motion brief' : 'Motion prompt'), ta2));
  }
  fields.push(
    h('div', { class: 'field' }, h('span', { class: 'label' }, sh.source !== 'procedural' ? 'Fallback scene' : 'Scene'),
      h('select', { 'aria-label': 'Scene', onchange: e => set('scene', e.target.value) }, ...SCENES.map(s => h('option', { value: s, selected: sh.scene === s }, s)))),
    h('div', { class: 'field' }, h('span', { class: 'label' }, 'Palette'), h('div', { class: 'swatches' }, ...sh.palette.map((c, i) => h('input', { type: 'color', value: c.length === 4 ? '#' + [...c.slice(1)].map(x => x + x).join('') : c, 'aria-label': `Palette color ${i + 1}`, onchange: e => { sh.palette[i] = e.target.value; set('palette', sh.palette); } })))),
    range('intensity', 'Intensity'), range('speed', 'Speed'), range('punch', 'Beat punch'),
    h('div', { class: 'field' }, h('span', { class: 'label' }, 'Transition in'), seg('transition_in', ['cut', 'fade', 'flash'])),
    h('div', { class: 'field' }, h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: sh.lyrics_overlay, onchange: e => set('lyrics_overlay', e.target.checked, 'lyrics') }), 'Show lyrics during this shot')));
  el.append(head(`Shot ${sh.id}`, h('span', { class: 'mono hint' }, sh.section)), h('div', { class: 'pane-b', style: 'padding-block:4px 10px' }, ...fields));
}
let boardTimer = null;
function putBoard(note) {
  clearTimeout(boardTimer);
  renderTimeline(); updateFrame();
  boardTimer = setTimeout(async () => {
    try { const r = await api('PUT', `/api/projects/${S.slug}/storyboard`, { storyboard: P().storyboard, note }); P().storyboard = r.storyboard; await refresh(); }
    catch (e) { toast(e.message, true); refresh(true); }
  }, 250);
}
async function approve(id, ok, note = '') {
  const sh = shots().find(s => s.id === id);
  try {
    await api('POST', `/api/projects/${S.slug}/approve`, { shots: [id], approved: ok, note });
    if (!ok) run(sh && sh.source === 'claude' ? 'draw' : 'keyframes', { shots: [id], redo: true, note }); else await refresh();
  }
  catch (e) { toast(e.message, true); }
}

// ---------------- timeline ----------------
const pct = t => `${(t / Math.max(0.001, dur())) * 100}%`;
function renderTimeline() {
  const tr = $('#tracks'); tr.innerHTML = ''; const d = P();
  if (!d.analysis) { tr.style.minWidth = '0'; tr.append(h('div', { class: 'place', style: 'grid-column:1/-1' }, h('b', {}, 'The timeline appears once the song is aligned'), h('span', {}, 'Every step hangs off song time: bars, waveform, sections, lyrics and shots.'))); return; }
  tr.style.minWidth = '';
  const lane = (name, cls) => { tr.append(h('div', { class: 'tname' }, name)); const l = h('div', { class: `lane ${cls}` }); tr.append(l); return l; };
  const ruler = lane('Bars', 'l-ruler');
  d.analysis.downbeats.forEach((b, i) => ruler.append(h('div', { class: 'tick', style: `left:${pct(b)}` }, i % 2 === 0 ? h('span', {}, String(i + 1)) : null)));
  const wv = lane('Wave', 'l-wave'); const c = h('canvas', { class: 'wave' }); wv.append(c);
  const sec = lane('Sections', 'l-sec');
  const kc = { intro: '--sec-intro', verse: '--sec-verse', chorus: '--sec-chorus', 'pre-chorus': '--sec-verse', bridge: '--sec-inst', instrumental: '--sec-inst', outro: '--sec-outro', hook: '--sec-chorus' };
  d.analysis.sections.forEach(s => sec.append(h('div', { class: 'blk sec', style: `left:${pct(s.start)};width:calc(${pct(s.end - s.start)} - 2px);--k:var(${kc[s.kind] || '--sec-intro'});--e:${s.energy}` }, s.label)));
  const lyr = lane('Lyrics', 'l-lyr');
  lines().forEach((ln, li) => lyr.append(h('div', { class: `blk lyr ${lineAllowed(li) ? '' : 'off'}`, 'data-li': li, style: `left:${pct(ln.start)};width:${pct(Math.max(0.3, ln.end - ln.start))}`, title: lineAllowed(li) ? ln.text : `${ln.text} (not shown on screen)`, onclick: e => { e.stopPropagation(); seek(ln.start - 0.3); } }, ln.text)));
  const shotLane = lane('Shots', 'l-shot');
  shots().forEach((sh, k) => {
    const g = sh.source !== 'procedural'; const f = d.shot_files[sh.id] || {}; const img = shotImg(sh);
    shotLane.append(h('div', { class: `blk shot ${S.sel === sh.id ? 'sel' : ''}`, style: `left:${pct(sh.start)};width:calc(${pct(sh.end - sh.start)} - 2px);${img ? `background-image:url(${img})` : 'background:var(--panel-2)'}`, onclick: e => { e.stopPropagation(); select(sh.id); } },
      h('span', { class: 'id' }, sh.id), g ? h('span', { class: `gen ${f.clip ? 'ok' : ''}` }, f.clip ? 'CLIP' : sh.source === 'claude' ? 'DRAWN' : 'AI') : null));
    if (k > 0) { const ed = h('div', { class: 'edge', style: `left:${pct(sh.start)}`, title: 'Drag to move this cut' }); ed.addEventListener('pointerdown', e => dragEdge(e, k, ed, shotLane)); shotLane.append(ed); }
  });
  tr.append(h('div', { class: 'playhead', id: 'ph' }));
  if (S.slice) tr.append(h('div', { class: 'slice', id: 'slice' }));
  placeOverlays();
  requestAnimationFrame(() => drawWave(c));
  ruler.addEventListener('pointerdown', e => sliceDrag(e, ruler));
  [wv, sec, lyr, shotLane].forEach(l => l.addEventListener('click', e => { if (!e.target.closest('.edge')) seek(xToT(e, l)); }));
}
function placeOverlays() {
  const lane = $('#tracks .lane'); if (!lane) return; const x0 = lane.offsetLeft, w = lane.offsetWidth, D = Math.max(0.001, dur());
  const ph = $('#ph'); if (ph) ph.style.left = `${x0 + (S.t / D) * w}px`;
  const sl = $('#slice'); if (sl && S.slice) { sl.style.left = `${x0 + S.slice[0] / D * w}px`; sl.style.width = `${(S.slice[1] - S.slice[0]) / D * w}px`; }
}
const xToT = (e, el) => { const r = el.getBoundingClientRect(); return Math.min(dur(), Math.max(0, (e.clientX - r.left) / r.width * dur())); };
function dragEdge(e, k, ed, laneEl) {
  e.preventDefault(); e.stopPropagation(); ed.classList.add('drag'); ed.setPointerCapture(e.pointerId);
  const a = shots()[k - 1], b = shots()[k]; const before = b.start;
  const move = ev => { let t = snap(xToT(ev, laneEl)); t = Math.max(a.start + 1, Math.min(b.end - 1, t)); a.end = b.start = t; ed.style.left = pct(t);
    const els = laneEl.querySelectorAll('.shot'); els[k - 1].style.width = `calc(${pct(a.end - a.start)} - 2px)`; els[k].style.left = pct(t); els[k].style.width = `calc(${pct(b.end - b.start)} - 2px)`; };
  const up = () => { ed.removeEventListener('pointermove', move); ed.removeEventListener('pointerup', up); ed.classList.remove('drag');
    if (b.start !== before) putBoard(`Moved the cut before ${b.id} to ${fmt(b.start)}`); };
  ed.addEventListener('pointermove', move); ed.addEventListener('pointerup', up);
}
function sliceDrag(e, ruler) {
  const t0 = xToT(e, ruler); let moved = false; ruler.setPointerCapture(e.pointerId);
  const move = ev => { const t1 = xToT(ev, ruler); if (Math.abs(t1 - t0) > 0.5) { moved = true; S.slice = [Math.min(t0, t1), Math.max(t0, t1)].map(snap); if (!$('#slice')) $('#tracks').append(h('div', { class: 'slice', id: 'slice' })); placeOverlays(); } };
  const up = () => { ruler.removeEventListener('pointermove', move); ruler.removeEventListener('pointerup', up);
    if (!moved) { S.slice = null; $('#slice')?.remove(); seek(t0); }
    else if (S.slice[1] - S.slice[0] < 1) { S.slice = null; $('#slice')?.remove(); }
    else { toast(`Slice ${fmt(S.slice[0])} to ${fmt(S.slice[1])} selected. Render it from the Render step.`); if (S.stage === 'render') renderStage(); } };
  ruler.addEventListener('pointermove', move); ruler.addEventListener('pointerup', up);
}
function drawWave(c, withHead = true) {
  const wave = P()?.wave || []; if (!wave.length) return;
  const r = c.getBoundingClientRect(); const dpr = window.devicePixelRatio || 1;
  c.width = Math.max(10, r.width * dpr); c.height = Math.max(10, r.height * dpr);
  const g = c.getContext('2d'); const cs = getComputedStyle(document.documentElement);
  const n = wave.length, W = c.width, H = c.height, bw = W / n, D = dur();
  const cold = cs.getPropertyValue('--wave').trim(), hot = cs.getPropertyValue('--wave-hot').trim();
  for (let i = 0; i < n; i++) { const v = wave[i] * 0.9 + 0.05; g.fillStyle = withHead && (i / n) * D <= S.t ? hot : cold; const bh = v * H * 0.9; g.fillRect(i * bw + bw * 0.15, (H - bh) / 2, bw * 0.7, bh); }
}

// ---------------- playback ----------------
const aud = $('#aud');
aud.addEventListener('ended', () => setPlay(false));
function setPlay(on) {
  if (on && !aud.src) { toast('Import the audio first.', true); on = false; }
  S.playing = on;
  if (on) aud.play().catch(() => { S.playing = false; toast('The browser blocked playback. Click play again.', true); }); else aud.pause();
  $('#playIcon').innerHTML = on ? '<path d="M3 1.5h3.5v13H3zM9.5 1.5H13v13H9.5z"/>' : '<path d="M3 1.5v13l11-6.5z"/>';
  $('#play').setAttribute('aria-label', on ? 'Pause' : 'Play');
}
function seek(t) { S.t = Math.max(0, Math.min(dur() || 0, t)); try { aud.currentTime = S.t; } catch { } updateFrame(); }
$('#play').addEventListener('click', () => setPlay(!S.playing));
let curShot = null, curLine = -2, lastWave = 0;
function updateFrame() {
  if (!P()) return;
  const t = S.t;
  $('#tc').innerHTML = `${fmt(t)} <small>/ ${fmt(dur())}</small>`;
  placeOverlays();
  const sh = shotAt(t);
  const img = $('#monImg');
  if (sh && img && curShot !== sh.id) { curShot = sh.id; const u = shotImg(sh, true); if (u) img.src = u; const m = $('#monMeta'); if (m) m.textContent = `${sh.id} · ${sh.section}`; }
  const ov = $('#overlay');
  if (ov && P().timing) {
    let li = -1; lines().forEach((_, i) => { const [a, b] = lineWindow(i); if (t >= a && t <= b) li = i; });
    if (li !== curLine) { curLine = li; ov.innerHTML = ''; if (li >= 0 && lineAllowed(li)) lines()[li].words.forEach(wi => ov.append(h('span', {}, words()[wi].text), ' ')); }
    if (li >= 0 && lineAllowed(li)) {
      const [a, b] = lineWindow(li); const whole = lyricStyle() === 'lines';
      ov.style.opacity = Math.max(0, Math.min(1, (t - a) / (whole ? 0.12 : 0.25), (b - t) / 0.25));
      ov.querySelectorAll('span').forEach((sp, i) => {
        if (whole) { sp.style.opacity = 1; sp.classList.remove('on'); return; }
        const w = words()[lines()[li].words[i]]; const sung = Math.max(0, Math.min(1, (t - w.start) / 0.12)); sp.style.opacity = 0.32 + 0.68 * sung; sp.classList.toggle('on', t >= w.start && t <= w.end + 0.1); });
    } else ov.style.opacity = 0;
  }
  document.querySelectorAll('.lyr').forEach(el => { const ln = lines()[+el.dataset.li]; if (ln) el.classList.toggle('now', t >= ln.start && t <= ln.end + 0.3); });
  const k = $('#karaoke');
  if (k) {
    k.querySelectorAll('.kl[data-li]').forEach(row => { const ln = lines()[+row.dataset.li]; row.classList.toggle('now', t >= ln.start - 0.2 && t <= ln.end + 0.4); });
    k.querySelectorAll('button[data-w]').forEach(b => b.classList.toggle('sung', t >= words()[+b.dataset.w].start));
  }
  const w = $('.l-wave canvas'); if (w && performance.now() - lastWave > 120) { lastWave = performance.now(); drawWave(w); }
}
function loop() { if (S.playing) { S.t = aud.currentTime; updateFrame(); } requestAnimationFrame(loop); }
requestAnimationFrame(loop);

function select(id) {
  S.sel = id; const sh = shots().find(s => s.id === id);
  if (sh && (S.t < sh.start || S.t >= sh.end)) seek(sh.start + 0.05);
  if (['song', 'suno', 'timing', 'render'].includes(S.stage)) S.pickWord = null;
  renderTimeline(); renderInspector(); if (S.stage === 'look') renderStage();
}

// ---------------- jobs + history ----------------
function renderJobs() {
  const el = $('#jobs'); if (!el || !P()) return; el.innerHTML = '';
  const js = P().jobs; const running = js.filter(j => j.status === 'running').length, queued = js.filter(j => j.status === 'queued').length;
  const cnt = $('#jobCount'); cnt.innerHTML = '';
  cnt.append([running ? `${running} running` : '', queued ? `${queued} queued` : ''].filter(Boolean).join(' · ') || 'All quiet');
  if (queued > 1) cnt.append(' ', h('button', { class: 'btn ghost', style: 'padding:2px 8px;font-size:12px', onclick: async () => {
    try { const r = await api('POST', `/api/projects/${S.slug}/jobs/cancel-queued`); toast(`Cancelled ${r.cancelled.length} queued job${r.cancelled.length === 1 ? '' : 's'}.`); refresh(); } catch (e) { toast(e.message, true); } } }, 'Cancel all queued'));
  if (!js.length) { el.append(h('p', { class: 'empty' }, 'Nothing running. Renders, alignment, Claude and AI generation show up here with live progress.')); return; }
  js.slice(0, 8).forEach(j => {
    const done = j.status === 'done', err = j.status === 'error', queued = j.status === 'queued', gone = j.status === 'cancelled';
    const p = j.progress == null ? null : Math.round(j.progress * 100);
    const took = j.finished && j.started ? `${Math.round(j.finished - j.started)} s` : '';
    el.append(h('div', { class: `job ${done || gone ? 'done' : ''} ${err ? 'error' : ''}` },
      h('div', { class: 'top' }, h('b', {}, j.label), h('span', { class: 'mono' }, done ? `Done · ${took}` : err ? 'Failed' : gone ? 'Cancelled' : queued ? 'Queued' : p != null ? `${p}%` : 'Working'),
        queued ? h('button', { class: 'btn ghost', style: 'padding:1px 8px;font-size:12px', onclick: async e => {
          e.currentTarget.disabled = true;
          try { await api('POST', `/api/jobs/${j.id}/cancel`); toast(`Cancelled ${j.label}.`); refresh(); } catch (er) { toast(er.message, true); refresh(); } } }, 'Cancel') : null),
      gone ? null : h('div', { class: `bar ${!done && !err && !queued && p == null ? 'indet' : ''}` }, h('i', { style: `width:${done ? 100 : p || 0}%` })),
      j.message ? h('div', { class: `sub ${err ? 'err' : ''}` }, j.message) : null,
      j.live && !done && !err ? h('div', { class: 'thumb', style: `background-image:url(/files/${S.slug}/${j.live}?t=${Date.now()})`, title: 'Last frame rendered' }) : null,
      done && j.kind === 'render' && j.result?.file ? h('button', { class: 'btn', onclick: () => { S.renderSel = j.result.file; go('render'); renderStage(); } }, 'Watch') : null));
  });
}
function renderHist() {
  const el = $('#hist'); el.innerHTML = ''; const hs = P().history;
  if (!hs.length) { el.append(h('li', {}, h('span', { class: 'who' }, ''), h('span', {}, 'Every change, yours or Claude\'s, lands here.'))); return; }
  hs.slice(0, 16).forEach(x => el.append(h('li', {}, h('span', { class: `who ${x.who === 'claude' ? 'ai' : ''}` }, x.who), h('span', {}, x.text))));
}

// ---------------- Claude ----------------
function renderChat() {
  const el = $('#chat'); const msgs = S.chats[S.slug] || [];
  if (el.dataset.slug === S.slug && el.childElementCount === Math.max(1, msgs.length)) return;
  el.dataset.slug = S.slug; el.innerHTML = '';
  if (!msgs.length) el.append(h('div', { class: 'msg ai' }, keySet('ANTHROPIC_API_KEY')
    ? 'Ask for a change in plain words. I edit the storyboard or song and the timeline updates. I can queue stills and previews, but never spend on video without you.'
    : h('span', {}, 'Add an Anthropic key in ', h('a', { href: '#/settings' }, 'API keys'), ' and I can direct from here.')));
  msgs.forEach(m => el.append(msgEl(m)));
  el.scrollTop = el.scrollHeight;
}
function msgEl(m) {
  if (m.me) return h('div', { class: 'msg me' }, m.text);
  return h('div', { class: 'msg ai', style: m.err ? 'border-color:var(--bad)' : '' }, m.text,
    m.changes?.length ? h('span', { class: 'diff' }, m.changes.join('\n')) : null,
    m.ai ? h('span', { class: 'diff', style: 'opacity:.7' }, m.ai) : null,
    m.turn ? h('button', { class: 'btn ghost undo', disabled: m.undone, onclick: async e => {
      try { await api('POST', `/api/projects/${S.slug}/chat/undo`, { turn: m.turn }); m.undone = true; e.target.disabled = true; e.target.textContent = 'Undone'; refresh(true); } catch (er) { toast(er.message, true); } } }, m.undone ? 'Undone' : 'Undo') : null);
}
$('#ask').addEventListener('submit', async e => {
  e.preventDefault(); const v = $('#askIn').value.trim(); if (!v || !S.slug) return; $('#askIn').value = '';
  const msgs = S.chats[S.slug] = S.chats[S.slug] || [];
  msgs.push({ me: true, text: v }); const thinking = { text: 'Thinking...' }; msgs.push(thinking); renderChat(); $('#askBtn').disabled = true;
  try { const r = await api('POST', `/api/projects/${S.slug}/chat`, { message: v }); Object.assign(thinking, { text: r.reply || 'Done.', changes: r.changes, turn: r.turn, ai: r.ai }); }
  catch (er) { Object.assign(thinking, { text: er.message, err: true }); }
  $('#askBtn').disabled = false; $('#chat').innerHTML = ''; $('#chat').dataset.slug = ''; renderChat(); refresh(true);
});

// ---------------- library ----------------
async function renderLib() {
  const el = $('#libView'); el.innerHTML = '';
  let ps = []; try { ps = (await api('GET', '/api/projects')).projects; } catch (e) { toast(e.message, true); }
  const title = h('input', { class: 'text serif', style: 'font-size:18px', placeholder: 'Song title', 'aria-label': 'Song title' });
  const brief = h('textarea', { placeholder: 'Brief: who is it for, what happened, 3 to 5 concrete details, the mood.', 'aria-label': 'Brief' });
  const create = async () => { if (!title.value.trim()) return toast('Give it a title.', true);
    try { const r = await api('POST', '/api/projects', { title: title.value, brief: brief.value }); location.hash = `#/p/${r.slug}/song`; } catch (e) { toast(e.message, true); } };
  const grid = h('div', { class: 'lib' },
    h('div', { class: 'newform' }, h('b', { class: 'serif', style: 'font-size:22px;font-weight:400' }, 'New song'), title, brief,
      h('div', { class: 'wrap-gap' }, h('button', { class: 'btn primary', onclick: create }, 'Create'),
        h('button', { class: 'btn ghost', onclick: async () => { try { const r = await api('POST', '/api/demo'); location.hash = `#/p/${r.slug}/board`; } catch (e) { toast(e.message, true); } } }, 'Make a demo song'))),
    ...ps.map(p => h('a', { class: 'lcard', href: `#/p/${p.slug}`, style: 'text-decoration:none;color:inherit' },
      h('div', { class: 'im', style: p.cover ? `background-image:url(/files/${p.slug}/${p.cover})` : 'background:var(--panel-2)' }),
      h('div', { class: 'body' }, h('span', { class: 't' }, p.title), h('span', { class: 'm mono' }, [p.duration ? fmt(p.duration).slice(0, 5) : null, p.tempo ? `${Math.round(p.tempo)} BPM` : null, p.slug].filter(Boolean).join(' · ')),
        h('div', { class: 'mini' }, ...STAGES.map(([id]) => h('i', { class: p.status[id] || '' })))))));
  el.append(h('div', { class: 'status-line', style: 'margin-bottom:8px' }, h('strong', {}, 'Your songs'), ps.length ? '' : ' Nothing here yet. Start one, or make the demo to look around.'), grid);
}

// ---------------- settings (API keys) ----------------
async function renderSettings() {
  const el = $('#setView'); el.innerHTML = '';
  let data; try { data = await api('GET', '/api/keys'); } catch (e) { toast(e.message, true); return; }
  S.keys = data.keys;
  const card = k => {
    const inp = h('input', { class: 'text mono', type: 'password', placeholder: k.set ? `Replace the current key (${k.hint})` : `Paste your ${k.service} key`, autocomplete: 'off', 'aria-label': `${k.name} value` });
    const out = h('span', { class: 'hint' });
    const test = async () => { out.textContent = 'Testing...'; out.className = 'hint'; try { const r = await api('POST', `/api/keys/${k.name}/test`); out.textContent = r.message; out.className = r.ok ? 'hint' : 'hint err'; if (r.ok) out.style.color = 'var(--ok)'; } catch (e) { out.textContent = e.message; out.className = 'hint err'; } };
    const save = async () => { if (!inp.value.trim()) return toast('Paste the key first.', true);
      try { await api('PUT', `/api/keys/${k.name}`, { value: inp.value }); inp.value = ''; toast(`${k.name} saved.`); await renderSettings(); loadKeys(); } catch (e) { toast(e.message, true); } };
    return h('div', { class: 'keycard' },
      h('div', { class: 'top' }, h('b', {}, k.service), h('code', {}, k.name), h('span', { class: `state-pill ${k.set ? 'on' : ''}` }, k.set ? `Set${k.source ? ' in ' + k.source : ''}` : 'Not set')),
      h('span', {}, k.unlocks), h('span', { class: 'hint' }, k.billing, ' ', h('a', { href: k.get, target: '_blank', rel: 'noopener' }, 'Get a key')),
      k.source === 'shell' ? h('span', { class: 'hint' }, 'This one comes from your shell environment, so change it there.')
        : h('div', { class: 'row' }, inp, h('button', { class: 'btn primary', onclick: save }, 'Save'),
          k.set ? h('button', { class: 'btn ghost', onclick: async () => { await api('PUT', `/api/keys/${k.name}`, { value: '' }); toast(`${k.name} removed.`); renderSettings(); loadKeys(); } }, 'Remove') : null),
      h('div', { class: 'row', style: 'align-items:center' }, h('button', { class: 'btn', disabled: !k.set, onclick: test }, 'Test'), out));
  };
  el.append(h('div', { class: 'keys' },
    h('div', {}, h('b', { class: 'serif', style: 'font-size:26px;font-weight:400' }, 'API keys'),
      h('ol', { class: 'steps', style: 'margin-top:10px' },
        h('li', {}, h('b', {}, 'Anthropic first. '), 'Claude writes the songs and directs. Create a key, add a few dollars of credit, paste it below, then Test.'),
        h('li', {}, h('b', {}, 'fal.ai when you want AI imagery. '), 'Add credit, create a key, paste and Test. Nothing is spent until you approve a still and confirm on the Picture step.'),
        h('li', {}, h('b', {}, 'Suno API is optional. '), 'The manual flow (generate on suno.com, drop the MP3 in) needs no key.'),
        h('li', {}, 'Keys are stored in ', h('code', {}, data.env_file), ' on this computer (owner-only permissions, ignored by git). A key set in your shell wins over that file.'))),
    ...data.keys.map(card),
    data.models ? h('div', { class: 'keycard' }, h('div', { class: 'top' }, h('b', {}, 'Models in use')),
      h('dl', { class: 'kv' },
        h('dt', {}, 'Claude'), h('dd', { class: 'mono' }, data.models.claude),
        h('dt', {}, 'AI stills'), h('dd', { class: 'mono' }, data.models.image),
        h('dt', {}, 'AI video'), h('dd', { class: 'mono' }, data.models.video),
        h('dt', {}, 'Whisper'), h('dd', { class: 'mono' }, data.models.whisper)),
      h('span', { class: 'hint' }, 'Change these in songvid.yaml (llm.model, generate.image_model, generate.video_model, align.whisper_model). Each Claude job card shows the model that actually answered, which can differ if a safety fallback kicked in.')) : null));
}

// ---------------- keyboard + theme ----------------
document.addEventListener('keydown', e => {
  if (e.target.closest('input, textarea, select, [contenteditable]') || S.route !== 'project') return;
  if (e.code === 'Space') {
    e.preventDefault();
    if (S.tap && S.stage === 'timing') { S.tap.taps.push(+aud.currentTime.toFixed(3)); S.tap.i++; if (S.tap.i >= lines().length) { setPlay(false); finishTap(); } renderStage(); return; }
    setPlay(!S.playing); return;
  }
  if (S.stage === 'look' && P()?.storyboard) {
    const list = shots(); const i = list.findIndex(s => s.id === S.sel);
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { select(list[(i + (e.key === 'ArrowRight' ? 1 : -1) + list.length) % list.length].id); }
    const sh = list[i];
    if (sh && sh.source !== 'procedural' && (P().shot_files[sh.id] || {}).key && (e.key === 'a' || e.key === 'r')) approve(sh.id, e.key === 'a');
  }
});
$('#theme').addEventListener('click', () => {
  const r = document.documentElement; const cur = r.dataset.theme || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
  r.dataset.theme = cur === 'dark' ? 'light' : 'dark'; try { localStorage.setItem('cuesheet-theme', r.dataset.theme); } catch { }
  if (S.route === 'project') renderTimeline();
});
try { const t = localStorage.getItem('cuesheet-theme'); if (t) document.documentElement.dataset.theme = t; } catch { }
window.addEventListener('resize', () => { placeOverlays(); const w = $('.l-wave canvas'); if (w) drawWave(w); });

parseRoute(); loadKeys().then(boot); connectEvents();
