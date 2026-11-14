// Build desk/app first. Run with PLAYWRIGHT_MODULE pointing at Playwright's
// index.mjs and optional CHROMIUM_EXECUTABLE; output directory is argv[2].
// Uses only synthetic loopback fixtures, no installed profile or model calls.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {ready,close,extendFixture} from './fixtures/ui-session-server.mjs';

const {chromium}=await import(process.env.PLAYWRIGHT_MODULE
  ?pathToFileURL(resolve(process.env.PLAYWRIGHT_MODULE)).href:'playwright');
const output=resolve(process.argv[2]||'window-retention-evidence');
await mkdir(output,{recursive:true});
const url=await ready;
let n=0,phase='silent',reads=0;
const id=i=>'retention-'+String(i).padStart(5,'0');
const room=i=>phase==='rooms'?'window-'+(i%2?'a':'b'):'window';
const row=i=>({id:id(i),kind:'chat_turn',status:i===n?'running':'done',chat_id:room(i),
  created_at:new Date(1700000000000+i*1000).toISOString(),goal_head:'Work '+i});
const payload=Array.from({length:4000},(_,i)=>'row '+i+' synthetic evidence stays available\n').join('');
extendFixture(undefined,undefined,async path=>{
  const p=new URL(path,'http://fixture').pathname;
  if(p==='/api/state')return {agent:'Fixture',owner:'Owner',level:'live',
    runner:{alive:true,busy:true,run:id(n),since:Date.now()/1000-10},brain:{configured:true},
    relay:{used:false,authorized:true},telegram:{enabled:false},alarms:[],anatomy:true};
  if(p==='/api/runs')return Array.from({length:Math.min(n+1,40)},(_,i)=>row(n-i));
  if(p==='/api/chats')return (phase==='rooms'?['window-a','window-b']:['window'])
    .map(peer_id=>({peer_id,title:peer_id,kind:'window',messages:1,mtime_ns:1}));
  // A silent completed run has no chat-turn row. Its former live card vanishes.
  if(p.startsWith('/api/chat-turns/'))return [];
  if(p==='/api/forge/tasks')return {active:[],recent:[],next_cursor:null};
  if(p==='/api/phone/activity')return {reviewers:[],recent_reviewers:[]};
  if(p.startsWith('/api/run/')){
    reads++;const rid=decodeURIComponent(p.split('/')[3]),i=Number(rid.slice(10));
    return {id:rid,manifest:row(i),origin:{text:'Synthetic request'},events:[],
      iterations:Array.from({length:4},(_,step)=>({n:step+1,model:'fixture',status:'completed',ms:10,
        text:'Progress '+rid+' step '+step,
        tools:[{tool:'read',args:{path:'fixture.txt'},result:{head:payload+rid+'/'+step,truncated:false}}]}))};
  }
});
const browser=await chromium.launch({headless:true,
  ...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
const results=[];
try {
  for(phase of ['silent','rooms']){
    n=0;reads=0;
    const context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage(),errors=[];
    page.on('pageerror',error=>errors.push(String(error)));
    await page.route('**/*',r=>r.request().url().startsWith(url)||r.request().url().startsWith('data:')?r.continue():r.abort());
    const cdp=await context.newCDPSession(page);await cdp.send('Performance.enable');
    const samples=[];
    const sample=async()=>{
      await cdp.send('HeapProfiler.collectGarbage');
      const m=Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(v=>[v.name,v.value]));
      const result={phase,n,reads,heapMiB:+(m.JSHeapUsedSize/2**20).toFixed(2),nodes:m.Nodes,listeners:m.JSEventListeners};
      samples.push(result);console.log(JSON.stringify(result));
      await writeFile(resolve(output,phase+'.json'),JSON.stringify(samples,null,2));
    };
    await page.goto(url);await page.locator('.now').waitFor();
    await page.evaluate(()=>dispatchEvent(new CustomEvent('frame-go',{detail:'talk'})));
    for(let iteration=1;iteration<=60;iteration++){
      n=iteration;
      await fetch(url+'/__fixture/set',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({run:id(n)})});
      if(phase==='rooms')await page.evaluate(key=>dispatchEvent(new CustomEvent('frame-open-room',{detail:{key,name:key}})),room(n));
      // Exercise the real 700ms archive debounce, not a storm which suppresses it.
      await page.waitForTimeout(900);
      await page.locator('#panel .turn[data-run="'+id(n)+'"]').waitFor();
      if(n%10===0)await sample();
    }
    n=60;
    // Release request timeout closures and delayed departure callbacks as well.
    await page.waitForTimeout(27000);await sample();
    assert.ok(reads>=60,phase+' actually read the changing run details');
    const later=samples.filter(s=>s.n>=30);
    assert.ok(Math.max(...later.map(s=>s.heapMiB))-Math.min(...later.map(s=>s.heapMiB))<3,
      phase+': heap keeps growing after the bounded detail cache fills');
    assert.ok(later.at(-1).nodes<8000,phase+': retired DOM is retained');
    // A live row still opens its complete original evidence after pruning.
    const full=page.locator('#panel .turn[data-run="'+id(n)+'"] .turn-full');
    await full.locator('summary').click();
    await full.locator('.turn-full-body').getByText('Progress '+id(n)+' step 0',{exact:true}).waitFor({state:'attached'});
    assert.ok((await full.locator('.turn-full-body').textContent()).includes('row 3999 synthetic evidence stays available'));
    assert.deepEqual(errors,[]);
    await page.screenshot({path:resolve(output,phase+'.png')});
    results.push({phase,samples,fullEvidencePreserved:true});
    await context.close();
  }
  await writeFile(resolve(output,'PASS.json'),JSON.stringify({results},null,2));
}finally{await browser.close();await close();}
