"""Execute the shipped Python bootstrap JavaScript, not just its source text."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import python_app

NODE=r'''
const vm=require('node:vm'),assert=require('node:assert/strict'),fs=require('node:fs');
const bridge=JSON.parse(fs.readFileSync(0,'utf8'));
class Storage {constructor(){this.items={}}setItem(k,v){this.items[k]=String(v)}removeItem(k){delete this.items[k]}getItem(k){return this.items[k]??null}}
const store=new Storage(),events=[];let current={seen:'agent-new',values:{'helene.look':'{"uiScale":0.8,"dayColor":"#ccddff"}'}};
const context={Storage,console,Promise,setTimeout:()=>0,CustomEvent:class{constructor(name,data){this.type=name;this.detail=data?.detail}},window:{addEventListener:()=>{},localStorage:store,dispatchEvent:e=>events.push(e)},location:{replace:()=>{}},encodeURIComponent};
context.fetch=async(url,opts)=>({ok:true,json:async()=>{
 if(url.endsWith('/events'))return [];
 const request=JSON.parse(opts.body);
 if(request.command==='appearance_read')return {result:{...current,ok:true}};
 if(request.command==='window_preferences'){store.items[request.args.key]=request.args.value;return {result:{...current,ok:true}};}
 throw Error('Unexpected command');
}});
vm.runInNewContext(bridge,context);
(async()=>{
 store.setItem('draft.main','keep owner words');
 for(let i=0;i<5;i++)await new Promise(setImmediate);
 context.window.__HELENE__.syncAppearance();
 for(let i=0;i<5;i++)await new Promise(setImmediate);
 assert.equal(JSON.parse(store.getItem('helene.look')).uiScale,.8);
 assert.equal(store.getItem('draft.main'),'keep owner words');
 assert.equal(events.filter(e=>e.type==='helene:appearance').length,1);
 console.log('BRIDGE_AGENT_DRAFT_POLL_PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
class Bridge(unittest.TestCase):
    def test_agent_change_then_draft_write_does_not_consume_unapplied_seen(self):
        with tempfile.TemporaryDirectory() as temporary:
            app=python_app.Application(Path(temporary),Path(temporary))
            try:
                result=subprocess.run(['node','-e',NODE],input=json.dumps(app.bridge()),text=True,capture_output=True,encoding='utf-8',timeout=20)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn('BRIDGE_AGENT_DRAFT_POLL_PASS',result.stdout)
            finally:app.server.server_close()
if __name__=='__main__':unittest.main()
