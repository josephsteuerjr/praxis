import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
const source = readFileSync(new URL('../../setup/ui/src/tips.ts', import.meta.url), 'utf8');
const {startSetupTips, SETUP_TIPS} = await import('data:text/javascript;base64,' + Buffer.from(stripTypeScriptTypes(source)).toString('base64'));
let nextId = 0;
const jobs = new Map();
const timers = {set: globalThis.setTimeout, clear: globalThis.clearTimeout};
globalThis.setTimeout = (fn, delay) => { jobs.set(++nextId, {fn, delay}); return nextId; };
globalThis.clearTimeout = (id) => jobs.delete(id);
let box, paused = false;
globalThis.document = {createElement() { box = { isConnected:true, textContent:'', classList:{add(){},remove(){}}, setAttribute(){}, remove(){this.isConnected=false;} }; return box; }};
const advance = (delay) => { const entry=[...jobs].find(([,job])=>job.delay===delay); assert.ok(entry); jobs.delete(entry[0]); entry[1].fn(); };
try {
  const stop = startSetupTips({append(){}}, () => paused);
  assert.match(box.textContent, /После установки открой «Онбординг»/);
  advance(8000); advance(300); assert.equal(box.textContent, SETUP_TIPS[1]);
  paused = true; advance(8000); assert.equal(box.textContent, SETUP_TIPS[1]);
  assert.equal(jobs.size, 1);
  paused = false; advance(8000); paused = true; advance(300);
  assert.equal(box.textContent, SETUP_TIPS[1], 'validation must take priority even during a fade');
  stop(); assert.equal(jobs.size, 0); assert.equal(box.isConnected, false);
} finally { globalThis.setTimeout = timers.set; globalThis.clearTimeout = timers.clear; delete globalThis.document; }
console.log('Installer tips: first onboarding hint, rotation, validation priority and cleanup — OK');
