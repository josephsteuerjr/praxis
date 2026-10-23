"""Execute conflict/queue recovery in the actual Python-window bootstrap."""
import importlib.util,json
from pathlib import Path
import subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'desk'))
import python_app
NODE=r'''
const vm=require('node:vm'),assert=require('node:assert/strict'),fs=require('node:fs');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
class Storage{constructor(){this.items={}}setItem(k,v){this.items[k]=String(v)}removeItem(k){delete this.items[k]}getItem(k){return this.items[k]??null}}
const store=new Storage(),events=[],sent=[];let current={seen:'agent-new',values:{'helene.look':'{"uiScale":0.8,"dayColor":"#ccddff"}'}};
let failOnce=['uncertain','during_recovery'].includes(input.scenario);
let releaseRead,markReading;const readGate=new Promise(r=>releaseRead=r),readStarted=new Promise(r=>markReading=r);
const owned=['helene.look','frame.theme','frame.rail','frame.panel','frame.rail.width','frame.panel.width','helene.zoom'];
const context={Storage,console:{error:()=>{}},Promise,setTimeout:()=>0,CustomEvent:class{constructor(name,data){this.type=name;this.detail=data?.detail}},window:{localStorage:store,dispatchEvent:e=>events.push(e)},location:{replace:()=>{}},encodeURIComponent};
context.fetch=async(url,opts)=>({ok:true,json:async()=>{
 if(url.endsWith('/events'))return [];
 const request=JSON.parse(opts.body);
 if(request.command==='appearance_read'){
  if(input.scenario==='during_recovery'){markReading();await readGate;}
  return {result:{...current,ok:true}};
 }
 if(request.command==='window_preferences'){
  const {key,value,seen}=request.args;
  if(owned.includes(key)){
   sent.push({key,value,seen});
   if(failOnce){failOnce=false;throw Error('transport unknown');}
   if(seen!==current.seen)return {result:{...current,ok:false,code:'conflict',error:'changed'}};
   current={seen:'manual-accepted',values:{...current.values,[key]:value}};
  }
  return {result:{...current,ok:true}};
 }
 throw Error('unexpected request');
}});
vm.runInNewContext(input.bridge,context);
const flush=async()=>{for(let i=0;i<12;i++)await new Promise(setImmediate)};
(async()=>{
 store.setItem('helene.look','{"uiScale":0.9}');
 if(input.scenario==='during_recovery'){
  await flush();store.setItem('helene.look','{"uiScale":0.9}');await readStarted;
  store.setItem('helene.look','{"uiScale":1.1}');releaseRead();
 } else if(input.scenario!=='draft')store.setItem('helene.look','{"uiScale":1.1}');
 store.setItem('draft.main','owner draft retained');
 await flush();context.window.__HELENE__.syncAppearance();await flush();
 assert.equal(sent.length,1,'queued stale appearance entry must be invalidated');
 assert.equal(JSON.parse(current.values['helene.look']).uiScale,.8,'agent canonical value must survive');
 assert.equal(JSON.parse(store.getItem('helene.look')).dayColor,'#ccddff','canonical values must be applied before acknowledgement');
 assert.equal(JSON.parse(store.getItem('helene.look')).uiScale,.8);
 assert.equal(store.getItem('draft.main'),'owner draft retained');
 // An intentional fresh edit after recovery remains possible.
 const fresh=JSON.parse(store.getItem('helene.look'));fresh.uiScale=.95;store.setItem('helene.look',JSON.stringify(fresh));await flush();
 assert.equal(sent.length,2);assert.equal(JSON.parse(current.values['helene.look']).uiScale,.95);
 assert.equal(JSON.parse(current.values['helene.look']).dayColor,'#ccddff');
 console.log('CONFLICT_QUEUE_CANONICAL_PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
class Conflict(unittest.TestCase):
    def run_bridge(self,module,scenario):
        with tempfile.TemporaryDirectory() as temp:
            app=module.Application(Path(temp),Path(temp))
            try:
                result=subprocess.run(['node','-e',NODE],input=json.dumps({'bridge':app.bridge(),'scenario':scenario}),text=True,capture_output=True,encoding='utf-8',timeout=20)
                if result.returncode==0 and 'CONFLICT_QUEUE_CANONICAL_PASS' not in result.stdout:
                    result.returncode=99;result.stderr+='JS scenario ended without its completion assertion'
                return result
            finally:app.server.server_close()
    def test_conflict_then_draft_and_poll(self):
        result=self.run_bridge(python_app,'draft');self.assertEqual(result.returncode,0,result.stderr)
    def test_conflict_invalidates_two_already_queued_appearance_writes(self):
        result=self.run_bridge(python_app,'queued');self.assertEqual(result.returncode,0,result.stderr)
    def test_uncertain_write_draft_and_poll_do_not_consume_unapplied_token(self):
        result=self.run_bridge(python_app,'uncertain');self.assertEqual(result.returncode,0,result.stderr)
    def test_manual_edit_issued_during_async_recovery_is_invalidated_too(self):
        result=self.run_bridge(python_app,'during_recovery');self.assertEqual(result.returncode,0,result.stderr)
    def test_published193_bridge_reproduces_both_reported_failures(self):
        path=ROOT/'_state/continuation-193-a163908e/package/Helene/app/python_app.py'
        if not path.is_file():self.skipTest('local previous published package not present')
        spec=importlib.util.spec_from_file_location('published193bridge',path);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        for scenario in ('draft','queued'):
            self.assertNotEqual(self.run_bridge(old,scenario).returncode,0,'published193 must reproduce the rejected state path')
if __name__=='__main__':unittest.main(verbosity=2)
