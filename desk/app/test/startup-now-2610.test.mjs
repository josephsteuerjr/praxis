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
const delayed = new Function('cfg', 'LOCAL_AGENT', 'localStorage', 'loadRooms', `
  let showSeq = 0;
  const S = {agent:'Fixture'}, shown = [];
  const show = view => { shown.push(view); ++showSeq; };
  const viewInner = {innerHTML:'', querySelector:() => false};
  const renderState = () => {};
  const esc = value => value;
  const window = {setTimeout:() => {}};
  ${fragment('  const firstShowSeq = showSeq;', '\n  openFirst();')}
  return {openFirst, shown, navigate:show};
`);
const fresh = new Function('cfg', 'opts', 'view', 'show', 'stateText', 'statePill', 'stateAction', `
  ${fragment('  if (opts.firstRun?.(view)) return;', '  connect();')}
  return 'connect';
`);
const storage = value => ({getItem:() => value});
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };

test('configured/legacy install starts Now without browser onboarding storage', () => {
  for (const needs_local_setup of [false, undefined]) {
    for (const marker of [null, 'done', 'old-version']) {
      assert.equal(initial({needs_local_setup}, {}, storage(marker)), 'now');
    }
  }
});
test('fresh setup uses onboarding even if an old browser marker says done', () => {
  assert.equal(initial({needs_local_setup:true}, {}, storage('done')), 'learn');
  const shown = [], text = {}, pill = {dataset:{}}, action = {};
  assert.equal(fresh({needs_local_setup:true}, {}, {}, view => shown.push(view), text, pill, action), undefined);
  assert.deepEqual(shown, ['learn']);
  assert.equal(action.hidden, true);
  assert.equal(pill.dataset.level, 'warn');
});
test('remote edition is not forced into local onboarding', () => {
  assert.equal(initial({needs_local_setup:true}, {localAgent:false}, storage(null)), 'now');
});
test('late startup response uses durable setup state, never browser marker', async () => {
  for (const needs_local_setup of [false, undefined, true]) {
    let resolve;
    const h = delayed({needs_local_setup}, true, storage(needs_local_setup ? 'done' : null), () => new Promise(r => {resolve=r;}));
    h.openFirst(); resolve(); await flush();
    assert.deepEqual(h.shown, [needs_local_setup ? 'learn' : 'now']);
  }
});
test('delayed startup rechecks current configuration at arrival', async () => {
  const cfg = {needs_local_setup:true}; let resolve;
  const h = delayed(cfg, true, storage(null), () => new Promise(r => {resolve=r;}));
  h.openFirst(); cfg.needs_local_setup = false; resolve(); await flush();
  assert.deepEqual(h.shown, ['now']);
});
test('manual onboarding remains available and late startup cannot replace it', async () => {
  let resolve;
  const h = delayed({needs_local_setup:false}, true, storage(null), () => new Promise(r => {resolve=r;}));
  h.openFirst();
  const manual = new Function('addEventListener', 'show', fragment('  addEventListener("frame-go",', '  addEventListener("frame-restart",'));
  manual((name, handler) => { assert.equal(name, 'frame-go'); handler({detail:'learn'}); }, h.navigate);
  resolve(); await flush();
  assert.deepEqual(h.shown, ['learn']);
});
