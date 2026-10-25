import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';

const source = readFileSync(new URL('../../ui-kit/window/views/settings-frame.ts', import.meta.url), 'utf8');
const start = source.indexOf('  const auto = el("div");');
const end = source.indexOf('  auto.append(autoToggle);', start);
assert.ok(start > 0 && end > start);
const directory = mkdtempSync(join(tmpdir(), 'helene-autostart-ui-'));
try {
  const moduleFile = join(directory, 'stand.ts');
  writeFileSync(moduleFile, `export function stand(shell) {
    const messages = [];
    const posix = false;
    const el = () => ({});
    const toast = text => messages.push(text);
    const humanError = error => ({text:error.message});
    const toggle = (label, value, action) => {
      const attrs = {'aria-checked':String(value)};
      return {disabled:false,attrs,label,
        setAttribute:(key,value) => {attrs[key]=value},
        removeAttribute:key => {delete attrs[key]},
        click:async function() {
          if (this.disabled) return;
          const value = attrs['aria-checked'] !== 'true';
          attrs['aria-checked'] = String(value);
          await action(value);
        }};
    };
    ${source.slice(start,end)}
    return {button:autoToggle,messages};
  }`);
  const { stand } = await import(pathToFileURL(moduleFile));
  const tick = () => new Promise(resolve => setTimeout(resolve,0));
  let current = false, settle;
  const calls = [];
  const running = stand(async (command,args) => {
    calls.push([command,args]);
    if (command === 'autostart_get') return current;
    await new Promise(resolve => {settle=resolve});
    current=args.on;
  });
  assert.equal(running.button.disabled,true);
  await tick();
  assert.equal(running.button.disabled,false);
  const pending = running.button.click();
  assert.equal(running.button.disabled,true);
  assert.equal(running.button.attrs['aria-busy'],'true');
  await running.button.click();
  assert.equal(calls.filter(([command]) => command === 'autostart_set').length,1);
  settle(); await pending;
  assert.equal(running.button.disabled,false);
  assert.equal(running.button.attrs['aria-checked'],'true');
  assert.equal(running.messages.at(-1),'Автозапуск включён');
  for (const initial of [false,true]) {
    const failed = stand(async command => {
      if (command === 'autostart_get') return initial;
      throw new Error('operation denied');
    });
    await tick(); await failed.button.click();
    assert.equal(failed.button.attrs['aria-checked'],String(initial));
    assert.equal(failed.button.disabled,false);
    assert.equal(failed.messages.at(-1),'operation denied');
  }
  let changedState = false;
  const partial = stand(async command => {
    if (command === 'autostart_get') return changedState;
    changedState = true;
    throw new Error('post-write error');
  });
  await tick(); await partial.button.click();
  assert.equal(partial.button.attrs['aria-checked'],'true');
  assert.equal(partial.messages.at(-1),'post-write error');
  let reads = 0;
  const unavailable = stand(async command => {
    if (command === 'autostart_set') return;
    if (++reads === 1) return true;
    throw new Error('readback unavailable');
  });
  await tick(); await unavailable.button.click();
  assert.equal(unavailable.button.attrs['aria-checked'],'true');
  assert.equal(unavailable.button.disabled,true);
  assert.equal(unavailable.messages.at(-1),'readback unavailable');
  const unknown = stand(async () => 'false');
  await tick();
  assert.equal(unknown.button.disabled,true);
  assert.match(unknown.messages.at(-1),/состояние автозапуска/);
  const mismatch = stand(async command => command === 'autostart_get' ? false : null);
  await tick(); await mismatch.button.click();
  assert.equal(mismatch.button.attrs['aria-checked'],'false');
  assert.equal(mismatch.messages.at(-1),'Изменение автозапуска не подтверждено');
  console.log('autostart UI: pending, authoritative success/rollback, unavailable readback PASS');
} finally {
  rmSync(directory,{recursive:true,force:true});
}
