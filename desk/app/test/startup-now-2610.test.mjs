import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import test from 'node:test';

const source = readFileSync(new URL('../../ui-kit/window/main.ts', import.meta.url), 'utf8');
function fragment(from, to) {
  const start = source.indexOf(from);
  const end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, `production startup fragment: ${from}`);
  const wrapped = stripTypeScriptTypes(`function productionFragment() {\n${source.slice(start, end)}\n}`);
  return wrapped.slice(wrapped.indexOf('{') + 1, wrapped.lastIndexOf('}'));
}
const initial = new Function('cfg', 'opts', 'localStorage', `
  const S = {view:'now'};
  let LOCAL_AGENT = true;
  const setProductName = () => {};
  const setLocalAgent = value => { LOCAL_AGENT = value; };
  ${fragment('  setProductName(opts.productName);', '  // Длинный результат')}
  return S.view;
`);
// Execute the actual initial navigation, request, and completion handlers, not
// a copy of their ordering. In particular the request can stay pending forever.
const startup = new Function('cfg', 'loadRooms', `
  let showSeq = 0;
  const LOCAL_AGENT = true, S = {agent:'Fixture'}, shown = [], states = [], timers = [];
  const viewInner = {innerHTML:'', querySelector:() => false};
  const show = view => { shown.push(view); ++showSeq; viewInner.innerHTML = view; };
  const renderState = (...args) => states.push(args);
  const esc = value => value;
  const window = {setTimeout:fn => timers.push(fn)};
  const connect = () => {}, refreshState = () => {};
  ${fragment('  connect();', '  void refreshPulse();')}
  return {shown, states, timers, viewInner, navigate:show};
`);
const fresh = new Function('cfg', 'opts', 'view', 'show', 'stateText', 'statePill', 'stateAction', `
  const LOCAL_AGENT = opts.localAgent !== false;
  ${fragment('  if (opts.firstRun?.(view)) return;', '  connect();')}
  return 'connect';
`);
const storage = value => ({getItem:() => value});
const flush = async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); };
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};

function manualNavigate(h, view) {
  const manual = new Function('addEventListener', 'show', fragment('  addEventListener("frame-go",', '  addEventListener("frame-restart",'));
  manual((name, handler) => { assert.equal(name, 'frame-go'); handler({detail:view}); }, h.navigate);
}

test('configured/legacy install starts Now without browser onboarding storage', () => {
  for (const needs_local_setup of [false, undefined]) {
    for (const marker of [null, 'done', 'old-version']) {
      assert.equal(initial({needs_local_setup}, {}, storage(marker)), 'now');
    }
  }
});
test('fresh setup also opens Now without connecting an unconfigured model', () => {
  assert.equal(initial({needs_local_setup:true}, {}, storage('done')), 'now');
  const shown = [], text = {}, pill = {dataset:{}}, action = {};
  assert.equal(fresh({needs_local_setup:true}, {}, {}, view => shown.push(view), text, pill, action), undefined);
  assert.deepEqual(shown, ['now']);
  assert.equal(action.hidden, true);
  assert.equal(pill.dataset.level, 'warn');
});
test('edition first-run interception is preserved before connecting', () => {
  let calls = 0;
  const result = fresh({needs_local_setup:false}, {firstRun:() => {calls++; return true;}}, {}, () => assert.fail('no shared page'), {}, {}, {});
  assert.equal(result, undefined);
  assert.equal(calls, 1);
});
test('remote edition is not forced into local onboarding', () => {
  assert.equal(initial({needs_local_setup:true}, {localAgent:false}, storage(null)), 'now');
});
test('configured startup opens Now before a slow or unavailable room read settles', () => {
  const roomRead = deferred();
  const h = startup({needs_local_setup:false}, () => roomRead.promise);
  assert.deepEqual(h.shown, ['now']);
  assert.equal(h.viewInner.innerHTML, 'now');
});
test('connection-refused room read leaves Now mounted and reports disconnected', async () => {
  const roomRead = deferred();
  const h = startup({needs_local_setup:false}, () => roomRead.promise);
  roomRead.reject(new TypeError('fetch failed: ERR_CONNECTION_REFUSED'));
  await flush();
  assert.deepEqual(h.shown, ['now']);
  assert.equal(h.viewInner.innerHTML, 'now');
  assert.deepEqual(h.states, [[null, false]]);
  assert.equal(h.timers.length, 0, 'ordinary room polling owns retries; no stale startup takeover');
});
test('room success refreshes an untouched initial Now page', async () => {
  const roomRead = deferred();
  const h = startup({needs_local_setup:false}, () => roomRead.promise);
  roomRead.resolve(); await flush();
  assert.deepEqual(h.shown, ['now', 'now']);
});
for (const view of ['learn', 'settings', 'talk']) {
  for (const outcome of ['resolve', 'reject']) {
    test(`late startup ${outcome} cannot replace manually selected ${view}`, async () => {
      const roomRead = deferred();
      const h = startup({needs_local_setup:false}, () => roomRead.promise);
      manualNavigate(h, view);
      roomRead[outcome](outcome === 'reject' ? new TypeError('connection refused') : undefined);
      await flush();
      assert.deepEqual(h.shown, ['now', view]);
      assert.equal(h.viewInner.innerHTML, view);
    });
  }
}
