import assert from 'node:assert/strict';
import { fixture } from './fixtures/scroller-clock.mjs';

function release(frameMs, workMs) {
  const h = fixture({ frameMs });
  h.pointer('pointerdown', 100);
  h.pointer('pointermove', 400);
  h.advance(200);
  h.pointer('pointerup', 400);
  // A callback finishes later than its rAF timestamp, without changing cadence.
  const now = performance.now, write = h.s.write.bind(h.s), frame = h.s.frame;
  let spent = 0;
  globalThis.performance = { now: () => now() + spent };
  h.s.write = () => { write(); spent = workMs; };
  h.s.frame = timestamp => { spent = 0; frame(timestamp); };
  const samples = [];
  for (let n = 0; n < 80; n++) { h.advance(frameMs); samples.push(h.s.debug().shown); }
  h.s.destroy();
  return samples;
}
for (const hz of [60, 120, 144]) {
  const baseline = release(1000 / hz, 0);
  for (const workMs of [2, 4]) {
    const loaded = release(1000 / hz, workMs);
    assert.deepEqual(loaded, baseline, `${hz}Hz, ${workMs}ms callback work changed the release trajectory`);
  }
}
console.log('Scroll release follows elapsed frame time at 60/120/144Hz despite callback work');
