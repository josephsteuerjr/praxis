import assert from 'node:assert/strict';
import {readFileSync, writeFileSync, mkdtempSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {parseHTML} from 'linkedom';

const {window, document}=parseHTML('<html><body></body></html>');
Object.assign(globalThis,{window,document,customElements:window.customElements,HTMLElement:window.HTMLElement,CustomEvent:window.CustomEvent});
const timers=new Map();let timer=0;
window.setTimeout=fn=>{timers.set(++timer,fn);return timer};window.clearTimeout=id=>timers.delete(id);
const messages=[],copied=[],calls=[];
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const el=(tag,cls='',text='')=>{const node=document.createElement(tag);node.className=cls;node.textContent=text;return node};
const button=(text,cls,fn)=>{const node=el('button',cls,text);node.addEventListener('click',fn);return node};
globalThis.__relayStand={el,button,copyText:async text=>{copied.push(text);return true},toast:text=>messages.push(text),
  humanError:error=>({text:error.message}),shell:()=>{throw Error('unexpected local shell')}};
const temp=mkdtempSync(join(tmpdir(),'helene-relay-'));
try {
  let source=readFileSync(new URL('../src/relay-accounts.ts',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,'');
  source='const {el,button,copyText,toast,humanError,shell,electron}=globalThis.__relayStand;\n'+source;
  const file=join(temp,'accounts.ts');writeFileSync(file,source);
  const {relayAccounts}=await import(pathToFileURL(file));
  let state={reachable:true,active_slot:'primary',browser_login:true,slots:[
    {slot:'primary',stored:true,loaded:true,serving:true},{slot:'secondary',stored:false,loaded:false,serving:false}]};
  let deferred,fail=false;
  const transport=async(command,args={})=>{
    calls.push({command,args});
    if(command==='relay_account') {
      if(deferred){const hold=deferred;deferred=undefined;return hold}
      if(fail)throw Error('status offline');
      return structuredClone(state);
    }
    if(command==='relay_login')state.login={pending:true,slot:args.slot,id:'observed-login',method:args.method,
      url:'https://auth.openai.com/codex/device',device_code:{user_code:'ABCD-EFGH',expires_at:Date.now()/1000+300,verification_url:'https://auth.openai.com/codex/device'}};
    if(command==='relay_cancel_login')state.login={pending:false};
    return {};
  };
  const accounts=relayAccounts(transport);document.body.append(accounts.element);await tick();
  const sections=[...accounts.element.querySelectorAll('section')];
  const find=(section,label)=>[...section.querySelectorAll('button')].find(b=>b.textContent===label);
  find(sections[1],'Войти по коду').click();await tick();
  assert.deepEqual(calls.find(row=>row.command==='relay_login').args,{slot:'secondary',method:'device_code'});
  assert.equal(sections[1].querySelector('code').textContent,'ABCD-EFGH');
  assert.equal(sections[0].querySelector('code').textContent,'');
  assert.equal(find(sections[0],'Сменить аккаунт по коду').disabled,true,'one login across both slots');
  find(sections[1],'Скопировать код').click();await tick();assert.deepEqual(copied,['ABCD-EFGH']);
  assert.match(sections[0].textContent,/Активен/,'replacement does not erase saved account');
  // An older status response may not resurrect a cancelled prompt.
  let release;deferred=new Promise(resolve=>{release=resolve});const reading=accounts.refresh();
  const stale=structuredClone(state);
  find(sections[1],'Отменить вход').click();await tick();
  assert.equal(sections[1].querySelector('code').textContent,'');
  release(stale);await reading;await tick();await accounts.refresh();
  assert.equal(calls.find(row=>row.command==='relay_cancel_login').args.login_id,'observed-login');
  assert.equal(sections[1].querySelector('code').textContent,'');
  state=stale;state.login.device_code.expires_at=Date.now()/1000-1;
  await accounts.refresh();assert.equal(sections[1].querySelector('code').textContent,'');assert.match(sections[1].textContent,/Срок кода истёк/);
  state.login={pending:false};state.browser_login=false;await accounts.refresh();
  assert.equal(find(sections[1],'Войти через браузер на этом ПК').hidden,true);
  fail=true;await accounts.refresh();assert.equal(find(sections[1],'Войти по коду').disabled,true);
  assert.match(accounts.element.textContent,/status offline/);
  fail=false;state.browser_login=true;await accounts.refresh();
  find(sections[1],'Войти через браузер на этом ПК').click();await tick();
  assert.equal(calls.filter(row=>row.command==='relay_login').at(-1).args.method,'browser');
  accounts.element.remove();assert.equal(timers.size,0,'dispose cancels polling');
  assert.equal(messages.some(x=>/undefined/.test(x)),false);
  // The server/phone adapter targets the selected authenticated HTTP channel.
  let serverState={available:true,browser_login:false,slots:[{slot:'secondary',stored:true,active:true}],
    login:{pending:true,slot:'secondary',id:'server-login',method:'device_code',url:'https://auth.openai.com/codex/device'}};
  const requests=[],opened=[];window.open=url=>opened.push(url);
  let adapterSource=readFileSync(new URL('../../ui-kit/relay-subscription.ts',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,'');
  adapterSource='const {shell,electron}=globalThis.__relayStand;\n'+adapterSource;
  const adapterFile=join(temp,'adapter.ts');writeFileSync(adapterFile,adapterSource);
  const {subscriptionTransport}=await import(pathToFileURL(adapterFile));
  const remote=subscriptionTransport(async(path,body)=>{requests.push({path,body});return structuredClone(serverState)});
  const remoteView=await remote('relay_account');
  assert.equal(remoteView.active_slot,'secondary');assert.equal(remoteView.browser_login,false);
  await remote('relay_login',{slot:'secondary',method:'device_code'});
  assert.deepEqual(requests.at(-1),{path:'/api/phone/subscription',body:{action:'login',slot:'secondary',method:'device_code',login_id:undefined}});
  await remote('relay_cancel_login',{slot:'secondary',login_id:'server-login'});
  assert.equal(requests.at(-1).body.login_id,'server-login');
  await assert.rejects(remote('open_login_page',{slot:'primary'}),/недоступна/);
  await remote('open_login_page',{slot:'secondary'});assert.deepEqual(opened,['https://auth.openai.com/codex/device']);
  serverState.login.url='https://auth.openai.com.evil.invalid/';await remote('relay_account');
  await assert.rejects(remote('open_login_page',{slot:'secondary'}),/Неверный адрес/);
} finally {rmSync(temp,{recursive:true,force:true});delete globalThis.__relayStand;}
console.log('Device login: both slots, copy, scoped cancel, stale status, expiry, browser fallback, disconnect PASS');
