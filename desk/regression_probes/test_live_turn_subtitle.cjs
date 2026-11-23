// Adapted symbol map for the exact Vite8 port build; original author test retained in raw snapshot.
// Execute the shipped bundle functions, not a reimplementation. No app/server needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const bundle = process.argv[2] || path.join(__dirname, '../static/assets/main-DdxsmMFy.js');
const source = fs.readFileSync(bundle, 'utf8');
function between(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a + start.length);
  assert(a >= 0 && b > a, `bundle seam: ${start}`);
  return source.slice(a, b);
}
const old = '1.5.9 подтверждена по файлу установки…';
const trigger = 'ну-ка, ответь плиз, я заскриню';
let rows = [], items;
const head = {textContent: ''};
const panel = {box: {isConnected: true}, inner: {}, room: 'current',
  head: {querySelector: () => head}, empty: {}, painted: true,
  list: {set: value => {items = value;}}, scroller: {preserve: fn => fn()}};
panel.box.firstElementChild = panel.inner;
const context = {console, Date, Map, String, Number,
  X: {room: 'current', roomName: 'Current chat', runs: [{id: 'current-run', goal_head: old}], evCache: new Map()},
  yn: panel, G: async () => rows, Vn: () => 'current-run',
  Wn: () => {}, Pn: () => {}, Gn: () => {},
  dn: s => s.replace(/\s+/g, ' ').trim(), ke: s => s, f: () => '12:00',
  sn: {chat_turn: 'сообщение'}, hn: () => 'steps', fn: s => s,
};
vm.createContext(context);
vm.runInContext(between('function pn(', 'function hn('), context);
vm.runInContext(between('async function Un(', 'async function Wn('), context);
// Baseline intentionally has no helper; Un still runs and demonstrates the defect.
if (source.includes('function jn('))
  vm.runInContext(between('function jn(', 'function Mn('), context);
vm.runInContext(between('function Mn(', 'async function Nn('), context);
function card(run, status = 'live') {
  const fields = {'.turn-meta': {}, '.turn-cause': {textContent: old, hidden: false}};
  return {dataset: {run, status}, querySelector: k => fields[k], fields};
}
(async () => {
  await context.Un();
  assert.equal(items[0].cause, '', 'uncached live run must not use history-contaminated goal_head');
  assert.equal(items[0].title, 'Работает…');
  context.X.evCache.set('current-run', {id: 'previous-run', origin: {text: old}});
  await context.Un();
  assert.equal(items[0].cause, '', 'wrong-id cached detail must not label current run');
  const detail = {id: 'current-run', origin: {text: trigger, source: 'authority'}};
  context.X.evCache.set('current-run', detail);
  await context.Un();
  assert.equal(items[0].cause, trigger, 'current run origin labels synthetic live row');
  const live = card('current-run');
  context.Mn(live, detail);
  assert.equal(live.fields['.turn-cause'].textContent, trigger, 'detail fetch refreshes live header');
  context.Mn(live, {id: 'previous-run', origin: {text: old}});
  assert.equal(live.fields['.turn-cause'].textContent, trigger, 'late foreign response ignored');
  context.Mn(live, {id: 'current-run', origin: {text: ''}});
  assert.equal(live.fields['.turn-cause'].hidden, true, 'unknown origin clears stale subtitle');
  rows = [{run_id: 'current-run', kind: 'chat_turn', in: trigger, out: 'Новый ответ', who: 'Егор', ts: 1}];
  context.Vn = () => '';
  await context.Un();
  assert.equal(items[0].status, 'done');
  assert.equal(items[0].title, 'Новый ответ');
  assert.equal(items[0].cause, `Егор: «${trigger}»`, 'history transition uses per-run input');
  const archived = card('current-run', 'done');
  archived.fields['.turn-cause'].textContent = items[0].cause;
  context.Mn(archived, detail);
  assert.equal(archived.fields['.turn-cause'].textContent, items[0].cause, 'history label preserved');
  // Execute polling too: subtitle refresh must not erase elapsed duration.
  vm.runInContext(between('async function Kn(', 'function qn('), context);
  const polled = card('current-run');
  polled.isConnected = true;
  panel.list.element = () => polled;
  context.X.view = 'talk';
  context.Vn = () => 'current-run';
  context.Nn = async () => detail;
  context.Hn = () => '12 с';
  context.bn = new Map();
  context.wn = false;
  context.dispatchEvent = () => {};
  context.CustomEvent = function () {};
  await context.Kn();
  assert.equal(polled.fields['.turn-cause'].textContent, trigger, 'poll refreshes current subtitle');
  assert.equal(polled.fields['.turn-meta'].textContent, '12 с · steps', 'poll preserves elapsed duration');
  console.log('PASS: live subtitle binding (uncached, previous state, wrong id, current origin, refresh, unknown, live-to-history, archive preservation)');
})().catch(error => {console.error(error); process.exitCode = 1;});
