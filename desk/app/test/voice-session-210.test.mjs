import {strict as assert} from 'node:assert';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const read=p=>readFileSync(new URL(p,import.meta.url),'utf8');
const {VoiceSession}=await import('data:text/javascript;base64,'+Buffer.from(stripTypeScriptTypes(read('../../ui-kit/window/voice-session.ts'),{mode:'transform'})).toString('base64'));
const {VoiceArrivals}=await import('data:text/javascript;base64,'+Buffer.from(stripTypeScriptTypes(read('../../ui-kit/window/voice-arrivals.ts'),{mode:'transform'})).toString('base64'));
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
const target={agent:'A',room:'one',name:'Первый чат'};
function harness({permission,failSend=false}={}){
 let clock=0,stops=0,mediaCalls=0;const sent=[],errors=[],states=[];
 const stream={getAudioTracks:()=>[],getTracks:()=>[{stop:()=>stops++}]};
 class Recorder {
  state='inactive';mimeType='audio/webm';
  start(){this.state='recording';} pause(){this.state='paused';} resume(){this.state='recording';}stop(){this.state='inactive';}
  requestData(){queueMicrotask(()=>this.ondataavailable?.({data:new Blob(['v'.repeat(1024)],{type:this.mimeType})}));}
 }
 const session=new VoiceSession({media:()=>{mediaCalls++;return permission||Promise.resolve(stream);},recorder:()=>new Recorder(),
  send:async(blob,target)=>{if(failSend)throw Error('network unavailable');sent.push({blob,target});},changed:s=>states.push(s.state),error:s=>errors.push(s),now:()=>clock});
 return {session,sent,errors,states,stream,time:n=>clock=n,stops:()=>stops,mediaCalls:()=>mediaCalls,recover:()=>failSend=false};
}
{
 const h=harness();await h.session.hold(target,'pause');h.time(1400);h.session.release();await flush();
 assert.equal(h.session.state,'paused');assert.equal(h.sent.length,0);assert.equal(h.session.seconds,1.4);
 await h.session.hold(target,'pause');h.time(2100);h.session.release();await flush();assert.equal(h.mediaCalls(),1);assert.equal(h.session.seconds,2.1);
 await h.session.send();assert.equal(h.sent.length,1);assert.deepEqual(h.sent[0].target,target);assert.equal(h.session.state,'idle');assert.equal(h.stops(),1);
}
{
 const h=harness();await h.session.hold(target,'send');h.time(1500);h.session.release();h.session.release();await flush();assert.equal(h.sent.length,1);assert.equal(h.session.state,'idle');
}
{
 const h=harness();await h.session.hold(target,'send');h.time(800);h.session.pause();h.session.release();await flush();assert.equal(h.sent.length,0);assert.equal(h.session.state,'paused');
 await h.session.hold(target,'send');h.time(1800);h.session.release();await flush();assert.equal(h.sent.length,1);
}
{
 const h=harness();await h.session.hold(target,'send');h.time(1000);h.session.cancel();h.session.release();await flush();assert.equal(h.sent.length,0);assert.equal(h.session.state,'idle');assert.equal(h.stops(),1);
}
{
 let resolve;const permission=new Promise(r=>resolve=r),h=harness({permission});const start=h.session.hold(target,'send');h.session.release();resolve(h.stream);await start;
 assert.equal(h.session.state,'idle');assert.equal(h.sent.length,0);assert.equal(h.stops(),1);
}
{
 const h=harness({failSend:true});await h.session.hold(target,'send');h.time(1000);h.session.release();await flush();assert.equal(h.session.state,'paused');assert.ok(h.session.blob.size);
 await h.session.hold({...target,room:'two'},'send');assert.equal(h.session.state,'paused');assert.equal(h.session.target.room,'one');assert.equal(h.sent.length,0);
 h.recover();await h.session.send();assert.equal(h.sent.length,1);assert.equal(h.sent[0].target.room,'one');
}
{
 const h=harness();await h.session.hold(target,'send');h.time(100);h.session.release();await flush();assert.equal(h.sent.length,0);assert.equal(h.session.state,'paused');h.session.cancel();
}
{
 const arrivals=new VoiceArrivals();const old={key:'old',at:'2020-01-01T00:00:00Z'},fresh={key:'new',at:new Date(Date.now()+1).toISOString()};
 assert.equal(arrivals.observe('room',[old],true).size,0);assert.deepEqual([...arrivals.observe('room',[old,fresh],true)],['new']);
 assert.equal(arrivals.observe('room',[fresh,old],true).size,0);assert.equal(arrivals.observe('other',[old,fresh],true).size,0);
 assert.equal(new VoiceArrivals().observe('room',[old,fresh],true).size,0,'reload never replays old audio');
}
console.log('PASS voice hold/pause/send/cancel, permission race, pinned recipient, retry preservation, arrival replay fences');
