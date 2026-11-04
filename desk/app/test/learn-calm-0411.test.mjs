import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';

const source = readFileSync(new URL('../../ui-kit/window/views/learn.ts', import.meta.url), 'utf8');
const state = { agentState: null, connected: false, platform: 'windows' };
let foreign = false;
const cfg = { needs_local_setup: false };
const escape = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
globalThis.__learnTest = { S: state, cfg, foreignHarness: () => foreign, esc: escape };
const stubs = `const {S,cfg,foreignHarness,esc}=globalThis.__learnTest;
const LOCAL_AGENT=true, isMacPlatform=p=>p==='macos', voiceHelp=()=>{}, copyText=async()=>true, mountConnectionGuide=()=>{}, api=async()=>[];`;
const text = stripTypeScriptTypes(source.replace(/^import .*;\r?\n/gm, '') + '\nexport {TASKS,INTRO,assemble,hero,toc};');
const { render, TASKS, INTRO, STARTERS, assemble, hero, toc } = await import('data:text/javascript;base64,' + Buffer.from(stubs + text).toString('base64'));
globalThis.matchMedia = () => ({ matches: true });

function rendered() {
  const container = { innerHTML: '', querySelectorAll: () => [], querySelector: () => null };
  return render(container).then(() => container.innerHTML);
}

test('quiet onboarding retains all twelve editable task templates and ten sections', async () => {
  const html = await rendered();
  assert.equal(TASKS.length, 12);
  assert.equal((html.match(/<article class="task"/g) || []).length, 12);
  assert.equal((html.match(/<section /g) || []).length, 10);
  for (const task of TASKS) {
    assert.ok(task.template.includes('<'), task.id + ' must remain an owner-filled frame');
    assert.ok(html.includes(`data-take="${task.id}"`), task.id + ' action retained');
    assert.ok(html.includes(`data-card="${task.id}"`), task.id + ' editable card retained');
  }
  for (const id of ['start','connect','map','tasks','flow','how','rhythm','contracts','economy','limits']) assert.ok(html.includes(`id="lrn-${id}"`));
  assert.equal((html.match(/class="btn btn-primary"/g) || []).length, 1, 'one primary first action');
  assert.doesNotMatch(html, /reveal-on|lrn-welcome-note|Твоё первое дело<br>|Четыре вещи/);
  assert.equal(STARTERS.length, 4);
});

test('table of contents matches section reading order and all useful destinations remain', async () => {
  const html = await rendered();
  const sections = [...html.matchAll(/<section[^>]* id="([^"]+)"/g)].map(m => m[1]);
  const jumps = [...toc().matchAll(/data-jump="([^"]+)"/g)].map(m => m[1]);
  assert.deepEqual(jumps, sections);
  for (const view of ['talk','now','plans','wakes','frame','files','journal','anatomy']) assert.ok(html.includes(`data-go="${view}"`));
  assert.ok(html.includes('data-voice-help'));
  assert.ok(html.includes('flow-play'));
});

test('the introduction keeps missing values as blanks and typed values verbatim', () => {
  assert.equal(assemble(INTRO, {querySelector: () => null}, 'intro'), INTRO);
  const values = new Map([[1, '  Егор  '], [3, 'делаю   проекты'], [5, 'не выдумывал факты']]);
  const root = { querySelector(selector) { const i = Number(selector.match(/data-hole="(\d+)"/)[1]); return {value: values.get(i) || ''}; } };
  assert.equal(assemble(INTRO, root, 'intro'), 'Меня зовут Егор. Я делаю проекты. Мне важно, чтобы ты не выдумывал факты.');
});

test('connection and model labels never invent data and escape long provider names', () => {
  assert.doesNotMatch(hero(), /hero-pill/);
  state.agentState = {runner: {alive: false}, brain: {model: 'long/provider/<script>alert(1)</script>'}};
  assert.match(hero(), /не на связи/);
  assert.match(hero(), /long\/provider\/&lt;script&gt;/);
  assert.doesNotMatch(hero(), /<script>/);
  state.agentState.runner.alive = true;
  assert.match(hero(), /is-live/);
  foreign = true; state.connected = false;
  assert.match(hero(), /не на связи/);
  state.connected = true;
  assert.match(hero(), /is-live/);
  cfg.needs_local_setup = true;
  assert.match(hero(), /Подключить модель/);
  foreign = false; state.agentState = null; cfg.needs_local_setup = false;
});
