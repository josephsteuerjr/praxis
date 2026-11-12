// 12.11 (owner): one WebView2 renderer of the window held 3.8 GB after 22.5 h and spent
// ~20 % of a core while the agent worked. Two causes, two guards:
// 1. every run's full detail (steps, tool outputs) stayed in S.evCache forever;
// 2. the live-turn dot pulsed an animated box-shadow — a style recalc and repaint on
//    every frame for as long as the agent worked.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { RecentMap } from '../../ui-kit/recent-map.ts';

const recent = new RecentMap(3);
for (const id of ['a', 'b', 'c']) recent.set(id, id.toUpperCase());
assert.equal(recent.get('a'), 'A');             // a is now the most recent
recent.set('d', 'D');
assert.deepEqual([...recent.keys()], ['c', 'a', 'd'], 'the least recently used entry leaves first');
recent.set('c', 'C2');
assert.deepEqual([...recent.keys()], ['a', 'd', 'c'], 'replacing an entry refreshes it');
for (let n = 0; n < 1000; n++) recent.set('run-' + n, 'x');
assert.equal(recent.size, 3, 'the cache never grows past its limit');
assert.equal(recent.get('absent'), undefined);

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
assert.match(read('../../ui-kit/window/state.ts'), /evCache: new RecentMap<string, unknown>\(\d+\)/,
  'the window run-detail cache is bounded again');
assert.match(read('../../ui-kit/phone.ts'), /const evCache = new RecentMap<string, RunDetail>\(\d+\)/,
  'the phone run-detail cache is bounded too');

// Animations that run all day while the agent works may only use compositor
// properties. (Onboarding/recording animations are page- and moment-local.)
const css = read('../../ui-kit/window/styles/turns.css') + read('../../ui-kit/window/styles/app.css');
for (const name of ['turn-pulse', 'pulse', 'st-breathe']) {
  const block = css.match(new RegExp('@keyframes ' + name + '\\s*\\{([\\s\\S]*?)\\n\\}'));
  assert.ok(block, 'keyframes ' + name + ' exist');
  const properties = [...block[1].matchAll(/([a-z-]+)\s*:/g)].map((m) => m[1]);
  assert.ok(properties.length && properties.every((p) => p === 'transform' || p === 'opacity'),
    name + ' animates only transform/opacity, not ' + properties.join(', '));
}
console.log('window memory: run details bounded, live indicators composited');
