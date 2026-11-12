'use strict';

const $ = (id) => document.getElementById(id);
let state = null;
let targetsDirty = false;
let pollTimer = null;
let loading = false;
let actionPending = false;
let loadPromise = null;
let reloadQueued = false;
let stateRevision = 0;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function dateLabel(timestamp) {
  return timestamp ? new Date(timestamp * 1000).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}) : 'Not yet';
}

function showMessage(id, text) {
  $(id).textContent = text;
  $(id).hidden = !text;
}

async function api(path, data) {
  const response = await fetch(path, data === undefined ? {cache: 'no-store'} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Tracker-Request': '1'},
    body: JSON.stringify(data)
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status}).`);
  return body;
}

function selectedTargets() {
  return [...$('target-options').querySelectorAll('input:checked')].map((input) => input.value);
}

function renderTargets() {
  if (targetsDirty) return;
  const legend = element('legend', 'sr-only', 'Branches to track');
  $('target-options').replaceChildren(legend);
  for (const target of state.available_targets) {
    const label = element('label', 'target-option');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.name = 'targets';
    input.value = target;
    input.checked = state.targets.includes(target);
    input.addEventListener('change', () => {
      targetsDirty = true;
      $('save-targets').disabled = selectedTargets().length === 0 || actionPending;
    });
    label.append(input, element('span', '', target));
    $('target-options').append(label);
  }
  $('save-targets').disabled = true;
}

function card(pr) {
  const article = element('article', 'pr-card');
  const top = element('div', 'pr-top');
  const star = element('button', 'star-button', '★');
  star.type = 'button';
  star.title = `Remove #${pr.number} from watchlist`;
  star.setAttribute('aria-label', star.title);
  star.disabled = actionPending;
  star.addEventListener('click', () => act('/api/remove', {number: pr.number}, `Removed #${pr.number} from your watchlist.`));
  const main = element('div', 'pr-main');
  const link = element('a', 'pr-title', pr.title || 'Waiting for the first check');
  link.href = `https://github.com/NixOS/nixpkgs/pull/${pr.number}`;
  link.target = '_blank';
  link.rel = 'noreferrer';
  const meta = element('div', 'meta');
  const labels = {open: 'Open', draft: 'Draft', merged: 'Merged', closed: 'Closed', unknown: 'Not checked'};
  meta.append(element('span', `badge ${pr.state}`, labels[pr.state] || 'Unknown'));
  if (pr.base) meta.append(element('span', 'base', `→ ${pr.base}`));
  main.append(element('span', 'pr-number', `NixOS/nixpkgs #${pr.number}`), link, meta);
  top.append(star, main);
  const branches = element('div', 'branches');
  for (const [name, result] of Object.entries(pr.branches)) {
    const stale = !!result.error || !!pr.error;
    const statusLabels = {present: 'Arrived', absent: 'Not yet', unknown: 'Not checked', waiting: pr.state === 'closed' ? 'Not merged' : 'Awaiting merge'};
    const symbols = {present: '✓', absent: '◷', unknown: '·', waiting: '·'};
    const branch = element('div', `branch ${stale ? 'error' : result.status}`);
    const label = statusLabels[result.status] || 'Unknown';
    branch.title = result.error || `${label}${result.checked_at ? ' · Checked ' + dateLabel(result.checked_at) : ''}`;
    branch.append(element('span', '', stale ? '!' : symbols[result.status]), element('span', '', name), element('span', 'branch-label', stale ? `${label} · stale` : label));
    branches.append(branch);
  }
  const footer = element('div', 'card-footer');
  const timestamp = element('span', '', `Last successful check: ${dateLabel(pr.last_success)}`);
  if (pr.last_attempt) timestamp.title = `Last attempt: ${dateLabel(pr.last_attempt)}`;
  const check = element('button', 'text-button', 'Check now ↻');
  check.type = 'button';
  check.disabled = state.running || actionPending || state.cooldown_until * 1000 > Date.now();
  check.addEventListener('click', () => act('/api/refresh', {number: pr.number}, `Checking #${pr.number}…`));
  footer.append(timestamp, check);
  article.append(top, branches);
  if (pr.error) article.append(element('p', 'card-error', pr.error));
  article.append(footer);
  return article;
}

function render() {
  renderTargets();
  $('pr-count').textContent = state.prs.length;
  $('empty-state').hidden = state.prs.length > 0;
  $('pr-list').replaceChildren(...state.prs.map(card));
  $('pr-list').setAttribute('aria-busy', String(state.running));
  $('list-status').textContent = state.running ? 'Checking GitHub…' : 'Saved on this machine';
  $('schedule').textContent = state.schedule;
  $('last-refresh').textContent = dateLabel(state.last_refresh?.finished_at);
  $('token-status').textContent = state.token_configured ? 'Token configured' : 'Public API · no token';
  const cooldown = state.cooldown_until * 1000 > Date.now();
  $('refresh-all').disabled = !state.prs.length || state.running || cooldown || actionPending;
  $('refresh-all').textContent = state.running ? 'Checking…' : '↻ Check all now';
  $('refresh-progress').textContent = cooldown ? `Paused until ${dateLabel(state.cooldown_until)}. Saved results are still available.`
    : state.running ? `Checking ${state.last_refresh?.checked || 0} of ${state.last_refresh?.total || state.prs.length} PRs…`
    : state.last_refresh?.error || '';
}

async function load(force = false) {
  if (loading) {
    reloadQueued = reloadQueued || force;
    return loadPromise;
  }
  loading = true;
  loadPromise = (async () => {
    do {
      reloadQueued = false;
      const revision = stateRevision;
      clearTimeout(pollTimer);
      try {
        const freshState = await api('/api/state');
        // A completed mutation invalidates reads started before it.
        if (revision !== stateRevision) continue;
        state = freshState;
        render();
        if (state.running) {
          pollTimer = setTimeout(load, 1500);
        } else {
          const remaining = state.cooldown_until * 1000 - Date.now();
          if (remaining > 0) pollTimer = setTimeout(load, Math.min(remaining + 50, 2147483647));
        }
      } catch (error) {
        if (revision !== stateRevision) continue;
        showMessage('error', `Cannot load saved results. Is the local tracker running? ${error.message}`);
        $('list-status').textContent = 'Connection unavailable';
      }
    } while (reloadQueued);
  })();
  try {
    await loadPromise;
  } finally {
    loading = false;
  }
}

async function act(path, data, message) {
  if (actionPending) return;
  actionPending = true;
  showMessage('error', '');
  $('add-button').disabled = true;
  $('save-targets').disabled = true;
  if (state) render();
  try {
    const response = await api(path, data);
    stateRevision += 1;
    if (path === '/api/targets') targetsDirty = false;
    if (path === '/api/prs') $('pr-input').value = '';
    const busy = response.status === 'busy' || response.refresh?.status === 'busy';
    showMessage('notice', busy ? 'A check is already running. Your changes are saved; check again when it finishes.' : message);
  } catch (error) {
    showMessage('error', error.message || 'The request failed. Try again.');
  } finally {
    actionPending = false;
    $('add-button').disabled = false;
    await load(true);
    if (targetsDirty) $('save-targets').disabled = selectedTargets().length === 0;
  }
}

$('add-form').addEventListener('submit', (event) => {
  event.preventDefault();
  act('/api/prs', {pr: $('pr-input').value}, 'Added to your watchlist. Checking its latest status…');
});
$('targets-form').addEventListener('submit', (event) => {
  event.preventDefault();
  act('/api/targets', {targets: selectedTargets()}, 'Targets saved. Use Check all now, or wait for the next background check.');
});
$('refresh-all').addEventListener('click', () => act('/api/refresh', {}, 'Checking your starred PRs…'));
$('reload').addEventListener('click', () => { showMessage('error', ''); load(); });
document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
load();
