'use strict';
const $ = id => document.getElementById(id);
let operatorKey = '', currentPlaylist = null, currentPreview = null, previewID = null;
const selectedLibraries = new Set();
const busy = new Set();
let libraryRows = [], libraryHasMore = false;
$('theme').value = document.documentElement.dataset.theme || 'system';
$('theme').addEventListener('change', () => {
  const value = $('theme').value;
  document.documentElement.dataset.theme = value;
  try { localStorage.setItem('harmonia-theme', value); } catch (_) { /* In-memory preference still works. */ }
});
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function status(message, error = false) {
  $('status').textContent = message;
  $('status').classList.toggle('error', error);
}
async function request(path, method = 'GET', body) {
  if (!operatorKey) throw new Error('Enter the operator API key first.');
  const response = await fetch('/api/' + path, {
    method, headers: {'X-API-Key': operatorKey, 'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const data = await response.json().catch(() => ({detail: 'Server returned an invalid response.'}));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : (Array.isArray(data.detail) ? data.detail.map(e => e.loc.join('.') + ': ' + e.msg).join('; ') : 'Request failed.'));
  return data;
}
function bind(id, action) {
  $(id).addEventListener('click', async () => {
    busy.add(id);
    $(id).disabled = true;
    try { await action(); } catch (error) { status(error.message, true); }
    finally { busy.delete(id); $(id).disabled = false; updateExport(); }
  });
}
async function runJob(path, payload) {
  const {job_id} = await request('jobs/' + path, 'POST', payload);
  status('Worker queued · ' + job_id + '. You can keep this tab open to follow the result.');
  for (let count = 0; count < 1800; count++) {
    await new Promise(resolve => setTimeout(resolve, 1000));
    const job = await request('jobs/' + job_id);
    if (job.status === 'complete') {
      if (!job.result?.ok) throw new Error(job.result?.error || 'Worker did not confirm completion.');
      return job.result;
    }
    if (job.status === 'not_found') throw new Error('Job expired or was not found.');
  }
  throw new Error('Worker is still running. Inspect job ' + job_id + ' before retrying.');
}
function trackLabel(t) { return t.recording.title + ' · ' + t.recording.artists.map(a => a.name).join(', '); }
function option(select, label, value) { const item = el('option', label); item.value = value; select.append(item); }
function duration(ms) { const s = Math.round((ms || 0) / 1000); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); }
function trackURL(t) {
  const spotify = t.providers.find(p => p.provider === 'spotify');
  if (spotify) return 'https://open.spotify.com/track/' + encodeURIComponent(spotify.id);
  const youtube = t.providers.find(p => p.provider === 'youtube');
  if (youtube) return 'https://music.youtube.com/watch?v=' + encodeURIComponent(youtube.id);
  const yandex = t.providers.find(p => p.provider === 'yandex');
  if (yandex && /^\d+:\d+$/.test(yandex.id)) {
    const [track, album] = yandex.id.split(':');
    return `https://music.yandex.ru/album/${album}/track/${track}`;
  }
  return null;
}
function spotifyAttribution(container, tracks) {
  container.replaceChildren();
  const track = tracks.find(t => t.providers.some(p => p.provider === 'spotify'));
  if (!track) return;
  const provider = track.providers.find(p => p.provider === 'spotify');
  const link = el('a', undefined, 'provider-attribution');
  link.href = 'https://open.spotify.com/track/' + encodeURIComponent(provider.id);
  link.target = '_blank'; link.rel = 'noopener noreferrer';
  const logo = el('img'); logo.src = '/static/spotify-logo.svg'; logo.alt = 'Spotify';
  logo.width = 80; logo.height = 24;
  link.append(logo, el('span', 'Open in Spotify ↗'));
  container.append(link);
}
function renderPlaylist(p) {
  currentPlaylist = p;
  $('result-title').textContent = p.title;
  const ms = p.tracks.reduce((n, t) => n + (t.recording.duration_ms || 0), 0);
  $('result-meta').textContent = `${p.tracks.length} recordings · ${duration(ms)} total · ${p.provider || 'canonical library'}`;
  $('warnings').replaceChildren(...p.warnings.map(w => el('p', w, 'warning')));
  spotifyAttribution($('result-attribution'), p.tracks);
  $('tracks').replaceChildren();
  p.tracks.forEach((t, i) => {
    const row = el('tr'); const title = el('td');
    const url = trackURL(t);
    const label = el(url ? 'a' : 'span', t.recording.title);
    if (url) { label.href = url; label.target = '_blank'; label.rel = 'noopener noreferrer'; }
    title.append(label);
    title.append(el('small', t.recording.artists.map(a => a.name).join(', ')));
    const mood = Object.entries(t.features.mood).sort((a,b) => b[1] - a[1])[0];
    row.append(el('td', String(i + 1).padStart(2, '0')), title,
      el('td', t.recording.versions.join(', ')), el('td', duration(t.recording.duration_ms)),
      el('td', t.features.bpm ?? '—'), el('td', [t.features.key, t.features.scale].filter(Boolean).join(' ') || '—'),
      el('td', mood ? `${mood[0]} ${Math.round(mood[1] * 100)}%` : '—'));
    $('tracks').append(row);
  });
  if (!p.tracks.length) { const row = el('tr'); const cell = el('td', 'No tracks satisfy these constraints.', 'empty'); cell.colSpan = 7; row.append(cell); $('tracks').append(row); }
  $('download').disabled = false;
}
async function refresh(append = false) {
  const pageSize = 200;
  if (append) {
    const page = await request('playlists?limit=200&offset=' + libraryRows.length);
    libraryRows.push(...page);
    libraryHasMore = page.length === pageSize;
  } else {
    const count = Math.max(1, Math.ceil(libraryRows.length / pageSize));
    const refreshed = [];
    for (let page = 0; page < count; page++) {
      const batch = await request('playlists?limit=200&offset=' + page * pageSize);
      refreshed.push(...batch);
      libraryHasMore = batch.length === pageSize;
      if (!libraryHasMore) break;
    }
    libraryRows = refreshed;
  }
  const libraries = libraryRows;
  $('load-more').hidden = !libraryHasMore;
  const existing = new Set(libraries.map(p => p.id));
  for (const id of selectedLibraries) if (!existing.has(id)) selectedLibraries.delete(id);
  const sourceValue = $('source-playlist').value;
  $('libraries').replaceChildren(); $('source-playlist').replaceChildren();
  if (!libraries.length) { $('libraries').append(el('p', 'No imported playlists yet.', 'empty')); option($('source-playlist'), 'Import or compile a playlist first', ''); }
  libraries.forEach((p, i) => {
    const row = el('div', undefined, 'library-row'); const checkbox = el('input');
    checkbox.type = 'checkbox'; checkbox.id = 'library-' + i; checkbox.checked = selectedLibraries.has(p.id);
    checkbox.addEventListener('change', () => checkbox.checked ? selectedLibraries.add(p.id) : selectedLibraries.delete(p.id));
    const label = el('label', p.title); label.htmlFor = checkbox.id;
    label.append(el('small', `${p.count} tracks / ${p.provider || 'canonical'}`));
    const open = el('button', 'View', 'text-button');
    open.setAttribute('aria-label', 'View ' + p.title);
    open.addEventListener('click', async () => { try { renderPlaylist(await request('playlist?id=' + encodeURIComponent(p.id))); } catch(e) { status(e.message, true); } });
    row.append(checkbox, label, open); $('libraries').append(row); option($('source-playlist'), p.title, p.id);
  });
  if (existing.has(sourceValue)) $('source-playlist').value = sourceValue;
  else if (sourceValue) invalidatePreview();
}
async function connections() {
  const providers = await request('providers'); $('providers').replaceChildren();
  providers.forEach(p => {
    const row = el('div', undefined, 'provider-row'); const name = el('div');
    name.append(el('h3', p.name), el('small', p.configured ? 'Configured' : 'Not configured', p.configured ? 'confidence' : 'muted'));
    const detail = el('div'); detail.append(el('code', p.authentication));
    detail.append(el('p', Object.entries(p.capabilities).filter(([,v]) => v).map(([k]) => k.replaceAll('_',' ')).join(' · '), 'muted'));
    const check = el('button', 'Read playlists', 'secondary'); check.disabled = !p.configured;
    check.addEventListener('click', async () => {
      check.disabled = true;
      try {
        const lists = await request('providers/' + p.name + '/playlists'); $('remote-playlists').replaceChildren(el('h3', p.name + ' playlists'));
        lists.forEach(item => { const b = el('button', 'Import ' + item.title, 'secondary'); b.addEventListener('click', async () => { b.disabled = true; try { await runJob('import', {provider:p.name, reference:item.id}); await refresh(); status('Playlist imported.'); } catch(e) { status(e.message,true); } finally { b.disabled = false; } }); $('remote-playlists').append(b); });
        status(`Connected to ${p.name}. ${lists.length} playlists available.`);
      } catch(e) { status(e.message,true); } finally { check.disabled = false; }
    });
    row.append(name,detail,check); $('providers').append(row);
  });
}
function invalidatePreview() { currentPreview = null; previewID = null; updateExport(); }
function updateExport() { $('export').disabled = busy.has('export') || !currentPreview?.ready || !previewID || !$('idempotency').value.trim(); }
function renderMatches(preview) {
  spotifyAttribution($('match-attribution'), [
    ...preview.source.tracks,
    ...preview.matches.flatMap(result => [result.match, ...result.alternatives].filter(Boolean).map(m => m.track))
  ]);
  $('match-list').replaceChildren();
  preview.matches.forEach((result, index) => {
    const source = preview.source.tracks[index]; const row = el('div', undefined, 'match-row'); const head = el('div', undefined, 'match-head');
    head.append(el('strong', '+ ' + trackLabel(source)), el('span', result.match ? `${Math.round(result.match.confidence * 100)}% / ${result.needs_review ? 'review required' : 'accepted'}` : 'No compatible match', 'confidence' + (result.needs_review ? ' review' : '')));
    row.append(head);
    row.append(el('p', result.match ? '→ ' + trackLabel(result.match.track) : 'Search the target catalogue to choose a recording.', 'muted'));
    row.append(el('p', [...(result.match?.reasons || []), ...result.reasons].join(' · '), 'reasons'));
    const choices = [result.match, ...result.alternatives].filter(Boolean).map(m => m.track);
    const select = el('select'); select.setAttribute('aria-label', 'Target match for ' + source.recording.title);
    choices.forEach((t, i) => option(select, trackLabel(t) + ' / ' + t.id, i));
    if (!choices.length) option(select, 'No candidate selected', '');
    row.append(select);
    const actions = el('div', undefined, 'match-actions'); const query = el('input'); query.value = source.recording.artists[0].name + ' ' + source.recording.title;
    query.setAttribute('aria-label', 'Search target catalogue for ' + source.recording.title);
    const search = el('button', 'Search', 'secondary'); const confirm = el('button', 'Confirm mapping', 'secondary'); confirm.disabled = !choices.length;
    search.addEventListener('click', async () => {
      search.disabled = true;
      try { const found = await request('providers/' + preview.provider + '/search?q=' + encodeURIComponent(query.value)); choices.splice(0, choices.length, ...found); select.replaceChildren(); choices.forEach((t,i) => option(select, trackLabel(t) + ' / ' + t.id,i)); confirm.disabled = !choices.length; if (!choices.length) option(select,'No results',''); }
      catch(e) { status(e.message,true); } finally { search.disabled = false; }
    });
    confirm.addEventListener('click', async () => {
      confirm.disabled = true;
      try { await request('matches/confirm', 'POST', {source, provider:preview.provider, target:choices[Number(select.value)]}); invalidatePreview(); status('Mapping saved. Generate a new dry-run to include the correction.'); row.append(el('p','Confirmed. Regenerate the dry-run.', 'confidence')); }
      catch(e) { status(e.message,true); confirm.disabled = false; }
    });
    actions.append(query,search,confirm); row.append(actions); $('match-list').append(row);
  });
  if (!preview.matches.length) $('match-list').append(el('p','The source playlist is empty.', 'empty'));
  updateExport();
}
document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.panel').forEach(panel => panel.hidden = panel.id !== button.dataset.tab);
  document.querySelectorAll('[data-tab]').forEach(b => { b.classList.toggle('active', b === button); if (b === button) b.setAttribute('aria-current','page'); else b.removeAttribute('aria-current'); });
}));
bind('connect', async () => { operatorKey = $('api-key').value; await refresh(); await connections(); $('api-key').value = ''; status('Workbench unlocked. Import a library or load the demo.'); });
bind('load-more', async () => { await refresh(true); status('More playlists loaded.'); });
bind('refresh', async () => { await refresh(); status('Library refreshed.'); });
bind('import', async () => { const result = await runJob('import',{provider:$('import-provider').value, reference:$('import-reference').value}); await refresh(); renderPlaylist(await request('playlist?id=' + encodeURIComponent(result.playlist_id))); status('Playlist imported.'); });
$('upload').addEventListener('change', async event => {
  try { const file = event.target.files[0]; if (!file) return; if (file.size > 5_000_000) throw new Error('Maximum JSON file size is 5 MB.'); await request('playlists','POST',JSON.parse(await file.text())); await refresh(); status('Canonical playlist imported.'); } catch(e) { status(e.message,true); }
});
bind('demo', async () => { const data = await fetch('/static/demo-library.json').then(r => r.json()); const saved = await request('playlists','POST',data); selectedLibraries.add(saved.playlist_id); await refresh(); renderPlaylist(await request('playlist?id=' + encodeURIComponent(saved.playlist_id))); status('Sample library imported. Its tracks are fictional and audio features are illustrative.'); });
bind('build', async () => { if (!selectedLibraries.size) throw new Error('Select at least one library.'); const constraints = await request('constraints/validate','POST',{yaml:$('constraints').value}); const result = await runJob('build',{constraints,playlist_ids:[...selectedLibraries]}); await refresh(); $('source-playlist').value = result.playlist_id; invalidatePreview(); renderPlaylist(await request('playlist?id=' + encodeURIComponent(result.playlist_id))); status('Compilation complete. Review warnings and the track order below.'); });
bind('preview', async () => { invalidatePreview(); const source = $('source-playlist').value, target = $('target-provider').value; if (!source) throw new Error('Choose a source playlist first.'); const result = await runJob('preview',{playlist_id:source,provider:target}); if (source !== $('source-playlist').value || target !== $('target-provider').value) throw new Error('Selection changed. Generate a new dry-run.'); previewID = result.preview_id; currentPreview = await request('previews/' + previewID); renderMatches(currentPreview); status(currentPreview.ready ? 'Dry-run ready. Review all additions before creating a private playlist.' : 'Review required. Confirm mappings, then generate a new dry-run.'); });
$('source-playlist').addEventListener('change', invalidatePreview); $('target-provider').addEventListener('change', invalidatePreview); $('idempotency').addEventListener('input',updateExport);
bind('export', async () => { const result = await runJob('export',{preview_id:previewID,idempotency_key:$('idempotency').value.trim()}); await refresh(); invalidatePreview(); status('Private playlist created: ' + result.playlist_id); });
bind('download', async () => { if (!currentPlaylist) return; const url = URL.createObjectURL(new Blob([JSON.stringify(currentPlaylist,null,2)],{type:'application/json'})); const link = el('a'); link.href = url; link.download = 'harmonia-playlist.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000); });
