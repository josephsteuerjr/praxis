import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { launchIntent, forwardLaunch } from '../../electron/src/launch.ts';
import { pathProblem } from '../src/mountdata.ts';

assert.equal(pathProblem('/home/owner/Documents', 'linux'), '');
assert.equal(pathProblem('~/Documents', 'linux'), '');
assert.ok(pathProblem('C:\\Users\\Owner', 'linux'));
assert.ok(pathProblem('Documents', 'linux'));
assert.equal(pathProblem('/Users/owner/Documents', 'macos'), '');
assert.equal(pathProblem('C:\\Users\\Owner', 'windows'), '');
assert.throws(() => launchIntent(['helene', '--panic', '--resume']));
assert.throws(() => launchIntent(['helene', '--agent']));
assert.equal(launchIntent(['helene', '--python-supervisor']).pythonSupervisor, true);
assert.equal(launchIntent(['helene']).pythonSupervisor, false);
await assert.rejects(forwardLaunch(['helene', '--python-supervisor'], async () => { throw new Error('must not call host'); }),
  /startup choice/);
const calls=[];
assert.equal(await forwardLaunch(['helene','--resume'], async (command,args) => {calls.push([command,args]); return 'accepted';}), 'accepted');
assert.deepEqual(calls, [['owner_control',{action:'resume'}]]);
await assert.rejects(forwardLaunch(['helene','--resume'], async () => {throw new Error('transfer busy');}), /transfer busy/);
await forwardLaunch(['helene','--agent','fixture'], async (command,args) => calls.push([command,args]));
assert.deepEqual(calls[1], ['switch_agent',{id:'fixture'}]);
const main=readFileSync(new URL('../../electron/src/main.ts',import.meta.url),'utf8');
assert.ok(main.indexOf('if (intent.action)') < main.indexOf('host = new HostClient('), 'initial owner control must precede engine bootstrap');
assert.match(main, /new HostClient\(executable, ROOT, log, intent.agent, intent.pythonSupervisor\)/);
assert.match(main, /app\.relaunch\(\{ args: process\.argv\.slice\(1\) \}\)/, 'restart must preserve the selected owner flag');
const retire = main.indexOf('if (name === "retire-window")');
assert.ok(retire >= 0 && main.indexOf('old?.destroy()', retire) < main.indexOf('host.invoke("window_retire_ack"', retire),
  'Electron must destroy its actual BrowserWindow before acknowledging retirement');
assert.ok(main.indexOf('rendererRetired = true;', retire) < main.indexOf('host.invoke("window_retire_ack"', retire));
assert.match(main, /function show\(\) \{\s*if \(rendererRetired \|\| !channel \|\| quitting\) return;/,
  'tray/activation cannot reopen an old URL after retirement');
assert.match(main, /if \(!rendererRetired\) \{\s*initScript = ready\.script;/,
  'late startup ready cannot resurrect a retired bootstrap URL');
const quitting = main.indexOf('app.on("before-quit"');
assert.ok(quitting >= 0 && main.indexOf('old?.destroy()', quitting) < main.indexOf('void host.close()', quitting)
  && main.indexOf('popup?.destroy()', quitting) < main.indexOf('void host.close()', quitting)
  && main.indexOf('if ((old && !old.isDestroyed())', quitting) < main.indexOf('void host.close()', quitting),
  'normal Exit must physically retire main and tray renderers before stopping Python Jobs');
const hostSource=readFileSync(new URL('../../electron/src/host.ts',import.meta.url),'utf8');
assert.match(hostSource, /pythonSupervisor \? \["--python-supervisor"\] : \[\]/);
if (process.platform === 'linux') {
  const { registerHooks } = await import('node:module');
  const hooks = registerHooks({
    resolve(specifier, context, next) {
      return specifier === 'electron' ? {url:'helene-test:electron',shortCircuit:true} : next(specifier,context);
    },
    load(url, context, next) {
      return url === 'helene-test:electron' ? {format:'module',source:'export const shell={}; export const Notification={};',shortCircuit:true} : next(url,context);
    },
  });
  try {
    const { runCommand } = await import('../../electron/src/commands.ts');
    const invocations=[];
    const context={host:{invoke:async(cmd,args)=>{invocations.push([cmd,args]); return {url:'https://updates.example.test/linux',newer:true,sha256:'zip digest'};}}};
    const notice=await runCommand('update_check',{},context);
    assert.equal(notice.url,'https://updates.example.test/linux');
    assert.equal(notice.package_manager,true);
    assert.equal(notice.sha256,'');
    for (const command of ['update_download','update_install']) {
      await assert.rejects(runCommand(command,{},context),/пакетный менеджер/);
    }
    assert.equal(invocations.length,1,'refused Linux installer operations must not reach the host');
  } finally { hooks.deregister(); }
}
console.log('Linux paths and Electron CLI forwarding PASS');
