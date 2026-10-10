import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';

// Use the installed Tauri SDK, including its automatic destroy after a close
// event, so this regression cannot pass by mocking close() as a simple return.
globalThis.window = {};
const { Window: TauriWindow } = await import('../../setup/ui/node_modules/@tauri-apps/api/window.js');
const permissions = JSON.parse(readFileSync(new URL('../../setup/capabilities/default.json', import.meta.url))).permissions;
const source = readFileSync(new URL('../../setup/ui/src/window-close.ts', import.meta.url), 'utf8');
const { setupWindowClose } = await import('data:text/javascript;base64,' + Buffer.from(stripTypeScriptTypes(source)).toString('base64'));
assert.ok(permissions.includes('core:window:allow-destroy'));
const config = JSON.parse(readFileSync(new URL('../../setup/capabilities/default.json', import.meta.url)));
assert.deepEqual(config.windows, ['main']);
assert.match(readFileSync(new URL('../../setup/ui/src/main.ts', import.meta.url), 'utf8'), /const requestClose = setupWindowClose\(win, closingDuringInstall/);

async function stand() {
  const callbacks = new Map();
  const calls = [];
  let busy = false, failures = 0, denyDestroy = false;
  // No real OS window: invoke routes through the actual SDK and exact ACL.
  window.__TAURI_INTERNALS__ = {
    invoke: async (command, args) => {
      calls.push(command);
      if (command === 'plugin:window|destroy') {
        assert.equal(args.label, 'main');
        if (denyDestroy || !permissions.includes('core:window:allow-destroy')) throw Error('destroy not allowed by ACL');
      }
      if (command === 'plugin:window|close') await emitClose();
      return null;
    },
  };
  const win = new TauriWindow('main', { skip: true });
  win.listen = async (event, handler) => { callbacks.set(event, handler); return () => {}; };
  async function emitClose() {
    const handler = callbacks.get('tauri://close-requested');
    assert.ok(handler, 'close guard must be registered');
    await handler({event:'tauri://close-requested', id:1, payload:null});
  }
  const request = setupWindowClose(win, () => busy, () => failures++);
  await Promise.resolve();
  return { request, emitClose, calls, setBusy: value => busy=value, deny: () => denyDestroy=true, failures: () => failures };
}

let s = await stand();
await s.request(); // custom wizard X: close -> closeRequested -> destroy
assert.deepEqual(s.calls, ['plugin:window|close', 'plugin:window|destroy']);
assert.equal(s.failures(), 0);
s = await stand();
await s.emitClose(); // native title-bar/Alt+F4 close request
assert.deepEqual(s.calls, ['plugin:window|destroy']);
s = await stand();
s.setBusy(true);
await s.request(); await s.emitClose();
assert.deepEqual(s.calls, [], 'an active update must remain open until cancellation/rollback finishes');
s.setBusy(false);
await s.emitClose();
assert.deepEqual(s.calls, ['plugin:window|destroy']);
s = await stand();
s.deny(); await s.request();
assert.equal(s.failures(), 1, 'a destroy refusal must be caught inside the event handler');
assert.deepEqual(s.calls, ['plugin:window|close', 'plugin:window|destroy'], 'SDK must not attempt a second unhandled destroy');
console.log('setup close: actual Tauri close/event/destroy, ACL, busy guard and failure recovery passed');
