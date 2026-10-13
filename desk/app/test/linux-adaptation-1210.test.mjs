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
const calls=[];
assert.equal(await forwardLaunch(['helene','--resume'], async (command,args) => {calls.push([command,args]); return 'accepted';}), 'accepted');
assert.deepEqual(calls, [['owner_control',{action:'resume'}]]);
await assert.rejects(forwardLaunch(['helene','--resume'], async () => {throw new Error('transfer busy');}), /transfer busy/);
await forwardLaunch(['helene','--agent','fixture'], async (command,args) => calls.push([command,args]));
assert.deepEqual(calls[1], ['switch_agent',{id:'fixture'}]);
const main=readFileSync(new URL('../../electron/src/main.ts',import.meta.url),'utf8');
assert.ok(main.indexOf('if (intent.action)') < main.indexOf('host = new HostClient('), 'initial owner control must precede engine bootstrap');
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
