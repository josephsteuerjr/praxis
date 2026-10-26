"""Real isolated host/Forge children; local deterministic provider, no live account.

Run in Linux Docker/server only: detached Forge children are unsafe under the
native Windows Codex command runner. This checks transport/lifecycle contracts,
not a real model's semantic review or an owner's installed profile.
"""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'desk'))
from localharness import code_transition, source_merge, source_update, source_update_host, python_source_updater
from localharness.package_notice import read,write

BASE='def value():\n    return 1\n\ndef mine():\n    return 1\n'
MINE=BASE.replace('return 1\n\n','return 2\n\n')
INCOMING=BASE.replace('return 1\n\n','return 99\n\n')
MERGED='def value():\n    return 99\n\ndef mine():\n    return 2\n'


@unittest.skipIf(os.name=='nt','Real detached Forge workers require Linux boundary')
class LegacyHostProcess(unittest.TestCase):
    def test_legacy_missing_api_resolved_plan_runs_incoming_merger_and_frozen_panel(self):
        with tempfile.TemporaryDirectory(prefix='update-host-224-') as temporary:
            root=Path(temporary);old=root/'old';new=root/'.helene-update-fixture/Helene'
            code=root/'authored';home=root/'profile'
            for p in (old/'tree',code,home/'memory',new/'app',new/'tree',new/'runtime/bin'):p.mkdir(parents=True)
            # Old Forge cannot coordinate review. The selected code must never
            # become an executable import path in the incoming host or workers.
            legacy='raise RuntimeError("AUTHORED_FORGE_MUST_NOT_EXECUTE")\n'
            for directory in (old/'tree',code):
                (directory/'forge.py').write_text(legacy)
                (directory/'upgrade_fixture.py').write_text(BASE if directory==old/'tree' else MINE)
            shutil.copytree(ROOT/'helene/core',new/'tree',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','.git'))
            (new/'tree/upgrade_fixture.py').write_text(INCOMING)
            shutil.copytree(ROOT/'desk/localharness',new/'app/localharness',ignore=shutil.ignore_patterns('__pycache__'))
            for name in ('python_migrate.py','python_app.py'):
                shutil.copy2(ROOT/'desk'/name,new/'app'/name)
            shutil.copy2(sys.executable,new/'runtime/bin/python3')
            write(old/'python-app.json',{'version':'2.1.1','delivery':'portable'})
            write(new/'python-app.json',{'version':'2.2.4','delivery':'portable'})
            write(old/'helene.json',{'tree':str(home),'code':str(code),'runner':'','setup_complete':True})
            write(new/'code-upstream.json',{'files':source_merge.inventory(new/'tree')})
            files={p.relative_to(new/'app').as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in (new/'app').rglob('*') if p.is_file()}
            write(new/'source-inputs.json',{'files':files})
            requests=[];errors=[]
            class Provider(BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def do_POST(self):
                    try:
                        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                        self.assert_path(body)
                        tools={t['function']['name'] for t in body['tools']}
                        system='\n'.join(str(m.get('content','')) for m in body['messages'] if m['role'] in ('system','developer'))
                        prior=[m for m in body['messages'] if m['role']=='assistant' and m.get('tool_calls')]
                        count=len(prior);call=None
                        if tools=={'merge_source'}:
                            digest=re.search(r'Input SHA256=([0-9a-f]{64})',system)[1]
                            sequence=[{'action':'read','path':'upgrade_fixture.py'},
                                      {'action':'resolve','path':'upgrade_fixture.py','content':MERGED},
                                      {'action':'finish'}]
                            if count<len(sequence):call=('merge_source',{**sequence[count],'expected_input_sha256':digest})
                            content='Candidate finished; independent review required.'
                        elif tools=={'inspect'}:
                            digest=re.search(r'Subject SHA-256: ([0-9a-f]{64})',system)[1]
                            if count==0:call=('inspect',{'action':'read','path':'resolved/upgrade_fixture.py'})
                            else:
                                returned=[m for m in body['messages'] if m['role']=='tool'][-1]['content']
                                assert MERGED in json.loads(returned)['text']
                            content=json.dumps({'subject_sha256':digest,'verdict':'clear','summary':'Deterministic fixture only','findings':[],'gaps':[]})
                        else:raise AssertionError('Unexpected general-agent tools: '+str(tools))
                        requests.append({'tools':sorted(tools),'path':self.path,'tool':call[0] if call else None})
                        message={'role':'assistant','content':None if call else content}
                        if call:message['tool_calls']=[{'id':'call_'+str(len(requests)),'type':'function','function':{'name':call[0],'arguments':json.dumps(call[1])}}]
                        finish='tool_calls' if call else 'stop'
                        result={'id':'fixture-'+str(len(requests)),'object':'chat.completion','created':int(time.time()),'model':'fixture-model',
                                'choices':[{'index':0,'message':message,'finish_reason':finish}],
                                'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}
                        if body.get('stream'):
                            delta=dict(message)
                            if call:delta['tool_calls']=[dict(message['tool_calls'][0],index=0)]
                            result['object']='chat.completion.chunk';result['choices']=[{'index':0,'delta':delta,'finish_reason':finish}]
                            raw=('data: '+json.dumps(result)+'\n\ndata: [DONE]\n\n').encode();kind='text/event-stream'
                        else:raw=json.dumps(result).encode();kind='application/json'
                        self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                    except BaseException as e:
                        errors.append(repr(e));self.send_error(500)
                def assert_path(self,body):assert self.path=='/v1/chat/completions',self.path
            server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                write(home/'memory/llm.json',{'frameworks':{'openai':{'api_key':'fixture','base_url':f'http://127.0.0.1:{server.server_port}/v1','api_mode':'chat_completions'}},
                      'roles':{role:{'framework':'openai','model':'fixture-model','max_tokens':512} for role in ('voice','evaluator')}})
                result=code_transition.preflight(old,new)
                self.assertEqual(result['plans'][0]['state'],'needs_attention')
                directory=Path(result['plans'][0]['directory'])
                source_merge.resolve(directory,'upgrade_fixture.py',side='incoming')
                source_merge.finish(directory,exe=Path(shutil.which('git')))
                operation=read(directory/'pipeline.json');operation['created']=time.time()-7200;write(directory/'pipeline.json',operation)
                before={p.name:p.read_bytes() for p in code.iterdir()}
                old_config=(old/'helene.json').read_bytes()
                (home/'memory/owner-data.txt').write_bytes(b'private fixture: preserve exact bytes')
                profile_before={name:(home/name).read_bytes() for name in ('memory/llm.json','memory/owner-data.txt')}
                # The real legacy entry prepares AFTER old-owner exit, with no
                # capable running agent. Preparation includes the actual host,
                # worker processes, independent panel and recoverable overlay.
                prepared=python_source_updater.prepare_install(old,new)
                self.assertFalse(errors,errors)
                accepted=source_update.validate_ready(directory)
                self.assertEqual(accepted['state'],'ready_to_apply')
                self.assertEqual((directory/'result/upgrade_fixture.py').read_text(),MERGED)
                self.assertEqual(before,{p.name:p.read_bytes() for p in code.iterdir()})
                self.assertEqual(read(old/'python-app.json')['version'],'2.1.1')
                self.assertEqual({tuple(r['tools']) for r in requests},{('merge_source',),('inspect',)})
                note,recovery=python_source_updater.install(old,new)
                self.assertEqual(str(recovery),prepared['recovery'])
                selected=read(old/'helene.json')
                self.assertEqual(read(old/'python-app.json')['version'],'2.2.4')
                self.assertEqual((Path(selected['code'])/'upgrade_fixture.py').read_text(),MERGED)
                self.assertEqual(selected['tree'],str(home))
                for name,data in profile_before.items():self.assertEqual((home/name).read_bytes(),data)
                self.assertEqual(before,{p.name:p.read_bytes() for p in code.iterdir()})
                python_source_updater.rollback(old,recovery)
                self.assertEqual(read(old/'python-app.json')['version'],'2.1.1')
                self.assertEqual((old/'helene.json').read_bytes(),old_config)
                self.assertEqual(before,{p.name:p.read_bytes() for p in code.iterdir()})
                print('REAL_LEGACY_HOST_PASS',json.dumps({'provider_calls':len(requests),'normal_agent_started':False,'old_source_unchanged':True,'canonical_review_validated':True,'actual_overlay_and_rollback':True,'profile_preserved':True,'native_gui_started':False}),flush=True)
            except BaseException:
                for record in home.glob('memory/.self-review/review-*/request.json'):
                    value=read(record);print('REVIEW_FAILURE',json.dumps({k:value.get(k) for k in ('execution','verdict','gaps','critics','error')}),flush=True)
                for log in new.parent.glob('source-review-*.log'):print(log.read_text(errors='replace')[-16000:],flush=True)
                for log in home.glob('memory/.forge/tasks/**/supervisor.log'):print(str(log),log.read_text(errors='replace')[-4000:],flush=True)
                raise
            finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
