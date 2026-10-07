import {strict as assert} from 'node:assert';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const source=readFileSync(new URL('../src/voicecard.ts',import.meta.url),'utf8');
// Execute the actual refresh closure with a delayed channel and deterministic clock.
const fragment=source.slice(source.indexOf('  const fresh ='),source.indexOf('  const start = async'));
const factory=new Function('api','box','say','sayVoice','setTimeout','clearTimeout',`
  let timer=null,refreshing=false,refreshAgain=false;
  const picker={el:{childElementCount:1}},speechPicker={el:{childElementCount:1}};
  const model='draft-model',enabled=true,speakVoice='draft-voice',speak=true;
  const status={},speechStatus={},speechBtn={};
  const humanError=e=>({text:String(e)});
  const drawPicker=()=>{throw Error('picker rebuilt')},drawVoices=drawPicker;
  ${stripTypeScriptTypes(fragment)}
  return {refresh,fresh};`);
function harness(){
  const calls=[],timers=new Map(),paint=[],speechPaint=[],box={isConnected:true};let serial=0;
  const api=()=>new Promise(resolve=>calls.push(resolve));
  const h=factory(api,box,v=>paint.push(v),v=>speechPaint.push(v),(fn,ms)=>{timers.set(++serial,{fn,ms});return serial},id=>timers.delete(id));
  return {...h,calls,timers,paint,speechPaint,box};
}
const state=(modelBusy,speechBusy)=>({model:'saved-model',enabled:false,download:modelBusy?{state:'running',updated_utc:new Date().toISOString()}:null,
  speech:{voice:'saved-voice',enabled:false,download:speechBusy?{state:'running',at:new Date().toISOString()}:null}});
for(const pair of [[true,false],[false,true],[true,true]]){
  const h=harness(),p=h.refresh();h.calls[0](state(...pair));await p;
  assert.equal(h.timers.size,1,'one timer for either or both downloads');
  assert.equal([...h.timers.values()][0].ms,2000);
  assert.equal(h.paint[0].model,'draft-model');assert.equal(h.speechPaint[0].voice,'draft-voice');
}
{
  const h=harness(),p=h.refresh();await h.refresh();await h.refresh();
  assert.equal(h.calls.length,1,'concurrent refresh is coalesced');
  h.calls[0](state(true,true));await p;
  assert.equal(h.timers.size,1);const t=[...h.timers.values()][0];assert.equal(t.ms,0);
  h.timers.clear();const p2=t.fn();assert.equal(h.calls.length,2);h.calls[1](state(false,false));await p2;
  await Promise.resolve();assert.equal(h.timers.size,0);
}
{
  const h=harness(),p=h.refresh();await h.refresh();h.box.isConnected=false;
  h.calls[0](state(true,true));await p;
  assert.equal(h.paint.length,0,'late detached response cannot paint');assert.equal(h.timers.size,0);
  h.box.isConnected=true;const p2=h.refresh();h.calls[1](state(false,false));await p2;
  assert.equal(h.timers.size,0,'detached rerun does not survive re-entry');
  assert.equal(h.fresh(new Date(Date.now()+60000).toISOString()),false);
}
console.log('voice refresh: STT/TTS, delayed single-flight, draft preservation and detached response OK');
