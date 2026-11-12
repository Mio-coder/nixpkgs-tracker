// Run the actual app with a minimal DOM transport and controlled HTTP timing.
// These tests protect browser state transitions, not layout or browser APIs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag;
    this.children = [];
    this.textContent = '';
    this.disabled = false;
    this.hidden = false;
    this.value = '';
    this.checked = false;
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute() {}
  addEventListener() {}
  querySelectorAll() {
    return this.children.flatMap((child) => [
      ...(child.tagName === 'input' && child.checked ? [child] : []),
      ...child.querySelectorAll()
    ]);
  }
}

function fixture(overrides = {}) {
  return {
    prs: [{id: 1, number: 123, title: 'Example', state: 'open', base: 'master',
      last_success: 1000, last_attempt: 1000, error: null,
      branches: {master: {status: 'waiting', checked_at: 1000, error: null}}}],
    available_targets: ['master', 'nixos-unstable'], targets: ['master'],
    running: false, last_refresh: null, cooldown_until: 0,
    token_configured: true, schedule: 'Every 6 hours', ...overrides
  };
}

function app() {
  const nodes = new Map();
  const requests = [];
  const timers = new Map();
  let timerId = 0;
  let now = 1000000;
  class Clock extends Date { static now() { return now; } }
  const context = vm.createContext({
    document: {
      hidden: false,
      getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); },
      createElement(tag) { return new Element(tag); },
      addEventListener() {}
    },
    Date: Clock,
    setTimeout(fn, delay) { timers.set(++timerId, {fn, delay}); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    fetch(url, options) {
      return new Promise((resolve) => requests.push({url, options, resolve}));
    }
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../tracker/static/app.js'), 'utf8'), context);
  return {context, nodes, requests, timers, advance(ms) { now += ms; },
    async respond(index, value) {
      requests[index].resolve({ok: true, json: async () => value});
      await new Promise(setImmediate);
    }};
}

test('a mutation queues a fresh read after an older pending GET', async () => {
  const ui = app();
  await ui.respond(0, fixture());
  const oldLoad = ui.context.load();
  const mutation = ui.context.act('/api/remove', {number: 123}, 'Removed');
  assert.equal(ui.requests[1].url, '/api/state');
  assert.equal(ui.requests[2].url, '/api/remove');
  await ui.respond(2, {status: 'removed'});
  await ui.respond(1, fixture());
  assert.equal(ui.requests.length, 4, 'must fetch again after the obsolete snapshot');
  assert.equal(ui.requests[3].url, '/api/state');
  await ui.respond(3, fixture({prs: []}));
  await Promise.all([oldLoad, mutation]);
  assert.equal(Number(ui.nodes.get('pr-count').textContent), 0);
  assert.equal(ui.nodes.get('pr-list').children.length, 0);
});

test('cooldown expiry schedules a local update and enables checking', async () => {
  const ui = app();
  await ui.respond(0, fixture({cooldown_until: 1002}));
  assert.equal(ui.nodes.get('refresh-all').disabled, true);
  assert.equal(ui.timers.size, 1, 'cooldown expiry needs a timer even when no refresh runs');
  const timer = [...ui.timers.values()][0];
  assert.ok(timer.delay >= 2000 && timer.delay <= 2500);
  ui.advance(3000);
  timer.fn();
  await ui.respond(1, fixture({cooldown_until: 1002}));
  assert.equal(ui.nodes.get('refresh-all').disabled, false);
  assert.equal(ui.nodes.get('refresh-progress').textContent, '');
});

test('unsaved target choices survive a progress update', async () => {
  const ui = app();
  await ui.respond(0, fixture());
  const options = ui.nodes.get('target-options').children;
  vm.runInContext('targetsDirty = true;', ui.context);
  const loading = ui.context.load();
  await ui.respond(1, fixture({running: true}));
  await loading;
  assert.equal(ui.nodes.get('target-options').children, options);
});
