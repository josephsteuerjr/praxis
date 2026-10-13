import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { test } from 'node:test';

const element = (tag) => ({
  tag, children: [], attrs: {}, listeners: {}, style: {}, dataset: {}, className: '', textContent: '',
  append(...children) { this.children.push(...children); },
  replaceChildren(...children) { this.children = children; },
  setAttribute(key, value) { this.attrs[key] = String(value); },
  getAttribute(key) { return this.attrs[key]; },
  addEventListener(key, fn) { (this.listeners[key] ||= []).push(fn); },
  click() { if (!this.disabled) for (const fn of this.listeners.click || []) fn(); },
  input(value) { this.value = value; for (const fn of this.listeners.input || []) fn(); },
  querySelectorAll(selector) { return all(this).filter(n => n.className === selector.slice(1)); },
});
const all = (node) => [node, ...node.children.flatMap(all)];
globalThis.document = { createElement: element };
const url = (source) => 'data:text/javascript;base64,' + Buffer.from(stripTypeScriptTypes(source)).toString('base64');
const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const textURL = url(read('../../ui-kit/text.ts'));
const domURL = url(read('../../ui-kit/dom.ts').replace('"./text"', JSON.stringify(textURL)));
const libURL = url(read('../../ui-kit/window/lib.ts')
  .replace('"../text"', JSON.stringify(textURL)).replace('"../dom"', JSON.stringify(domURL)));
const calls = [];
let postHook = null;
const state = { ok: true, path: '/opt/helene/helene.json', config: {}, editable: [], mtime_ns: '123',
  access: { agent_mode: 'sandbox', sandbox_enabled: true, session0: false, scope: 'host', socket_present: false } };
globalThis.__serverAccessApi = {
  async api(path) { calls.push(['get', path]); return structuredClone(state); },
  async post(path, body) { calls.push(['post', path, structuredClone(body)]); if (postHook) return postHook(); return { saved: ['access'], dropped: [],
      mtime_ns: '124', note: 'перезапусти агента' }; },
};
const apiURL = url('export const {api,post}=globalThis.__serverAccessApi;');
const source = read('../../remote/src/serveragent.ts')
  .replace('"../../ui-kit/window/api"', JSON.stringify(apiURL))
  .replace('"../../ui-kit/window/lib"', JSON.stringify(libURL))
  .replace('"../../ui-kit/dom"', JSON.stringify(domURL));
const { serverAgentCard } = await import(url(source));
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const inputFor = (card, label) => all(card.el).find(n => n.tag === 'label' && n.children.some(c => c.textContent === label)).children.find(n => n.tag === 'input');
const saveFor = card => all(card.el).find(n => n.textContent === 'Сохранить на сервере');

test('editing one field preserves every other server setting in its replaced block', async () => {
  calls.length = 0;
  state.config = { model: { model: 'old', key: 'test-key', base_url: 'https://example.test', framework: 'openai', effort: 'high' },
    telegram: { owner_id: 42, bot_token: 'test-token', proxy: 'https://proxy.test', allow_from: 'any' },
    owner: { name: 'old owner', room: 'main', extra: true } };
  const card = serverAgentCard(); await tick();
  inputFor(card, 'Модель').input('new');
  inputFor(card, 'Твой Telegram-id').input('84');
  inputFor(card, 'Имя владельца').input('new owner');
  saveFor(card).click(); await tick();
  assert.deepEqual(calls[1][2].config, { model: { ...state.config.model, model: 'new' },
    telegram: { ...state.config.telegram, owner_id: 84 }, owner: { ...state.config.owner, name: 'new owner' } });
});

test('edits made during a save survive and use the returned freshness on the next save', async () => {
  calls.length = 0;
  // The imported module captures this function, so defer its response through a shared hook.
  let release;
  postHook = () => new Promise(resolve => { release = resolve; });
  const card = serverAgentCard(); await tick();
  inputFor(card, 'Модель').input('first'); saveFor(card).click();
  inputFor(card, 'Модель').input('second');
  release({ saved: ['model'], dropped: [], mtime_ns: '456', note: 'saved' }); await tick();
  postHook = null;
  saveFor(card).click(); await tick();
  assert.equal(calls[2][2].mtime_ns, '456');
  assert.equal(calls[2][2].config.model.model, 'second');
  assert.equal(calls[2][2].config.model.key, 'test-key');
});

test('failed save retains edits and does not silently reload the server form', async () => {
  calls.length = 0;
  const card = serverAgentCard(); await tick();
  inputFor(card, 'Модель').input('retry me');
  postHook = async () => { throw Object.assign(new Error('freshness conflict'), { status: 409, code: 'conflict' }); };
  saveFor(card).click(); await tick();
  assert.equal(calls.filter(c => c[0] === 'get').length, 1);
  assert.equal(inputFor(card, 'Модель').value, 'retry me');
  postHook = null;
  saveFor(card).click(); await tick();
  assert.equal(calls[2][2].config.model.model, 'retry me');
});

test('actual server form sends mode and root request to server API with freshness, never local config', async () => {
  calls.length = 0;
  const card = serverAgentCard(); await tick();
  const nodes = all(card.el);
  nodes.find(n => n.textContent === 'Интерактивный').click();
  nodes.find(n => n.attrs.role === 'switch').click();
  nodes.find(n => n.textContent === 'Сохранить на сервере').click(); await tick();
  assert.deepEqual(calls, [['get', '/api/agent-config'], ['post', '/api/agent-config',
    { config: { access: { agent_mode: 'interactive', session0: true } }, mtime_ns: '123' }]]);
  assert.ok(nodes.some(n => n.textContent.includes('ещё не подключил root-брокер')));
  assert.ok(!calls.some(c => c[1] === 'config_save'));
});

test('old server without access capability renders no unsupported privilege control', async () => {
  state.access = null;
  const card = serverAgentCard(); await tick();
  assert.ok(!all(card.el).some(n => n.attrs.role === 'switch'));
});

test('actual Hélène edition delegates connected saved agent settings to server edition', async () => {
  const delegation = [];
  globalThis.__serverEdition = async context => { delegation.push(context); return { cards: ['server'] }; };
  globalThis.__remoteComputerCard = async context => { assert.equal(context.saved.mode, 'remote'); return 'remote-computer'; };
  // Dependency substitution: execute the real exported function with only the remote
  // collaborator mocked. The remote branch must return before any native shell call.
  const local = read('../src/settings-agent.ts').replace(/^import[^\n]*\n/gm, '');
  const { agentEdition } = await import(url('const serverEdition=globalThis.__serverEdition;const remoteComputerCard=globalThis.__remoteComputerCard;\n' + local));
  const context = { saved: { mode: 'remote' }, draft: { mode: 'local' } };
  assert.deepEqual(await agentEdition(context), { cards: ['server', 'remote-computer'] });
  assert.deepEqual(delegation, [context]);
});
