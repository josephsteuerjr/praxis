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
import subprocess
import signal
import zipfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'desk'))
from localharness import code_transition, source_merge, source_update, source_update_host, python_source_updater
from localharness.package_notice import read,write

BASE='def value():\n    return 1\n\ndef mine():\n    return 1\n'
MINE=BASE.replace('return 1\n\n','return 2\n\n')
INCOMING=BASE.replace('return 1\n\n','return 99\n\n')
MERGED='def value():\n    return 99\n\ndef mine():\n    return 2\n'
HELPER='def shared_helper():\n    return 7\n'
LOCAL_FEATURE='local_feature = True\n'


@unittest.skipIf(os.name=='nt','Real detached Forge workers require Linux boundary')
class LegacyHostProcess(unittest.TestCase):
    def _one_click(self,root,old,new,code,home,requests,errors):
        from t_source_admission_225 import view
        coupled=os.environ.get('HELENE_TEST_RELAY_CUSTODY')=='1'
        mode=os.environ.get('HELENE_TEST_SOURCE_MODE','preserve')
        if coupled:
            import socket
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));relay_port=sock.getsockname()[1]
            fixture=ROOT/'desk/tests/fixtures/update_relay_lifetime.py'
            executable=old/'helene-relay';executable.write_bytes(('#!'+sys.executable+'\n').encode()+fixture.read_bytes());executable.chmod(0o755)
            cfg=read(old/'helene.json');cfg['relay']={'enabled':True,'port':relay_port,'key':'fixture'};write(old/'helene.json',cfg)
            settings=read(home/'memory/llm.json');upstream=settings['frameworks']['openai']['base_url'].removesuffix('/v1')
            settings['frameworks']['openai']['base_url']=f'http://127.0.0.1:{relay_port}/v1';write(home/'memory/llm.json',settings)
        write(old/'python-app.json',{'version':'2.1.1','delivery':'portable','target':'linux-x86_64'})
        version=json.loads((ROOT/'desk/app/package.json').read_text())['version']
        write(new/'python-app.json',{'version':version,'delivery':'portable','target':'linux-x86_64'})
        for directory in (old/'tree',code):
            shutil.copy2(new/'tree/sitecustomize.py',directory/'sitecustomize.py')
        # Model bundled ._pth startup: incoming interpreter imports its shipped
        # sitecustomize before the helper, with normal bytecode writes enabled.
        launcher=new/'runtime/bin/python3'
        launcher.write_text('#!/bin/sh\nunset PYTHONDONTWRITEBYTECODE PYTHONPYCACHEPREFIX\n'
            'program=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)\n'
            'export PYTHONPATH="$program/tree:$program/app:$program/app/localharness"\n'
            'exec '+str(sys.executable)+' "\u0024@"\n')
        launcher.chmod(0o755)
        # A controlled GUI-readiness fixture only; real transaction and child
        # processes run unchanged. No native desktop acceptance is asserted.
        (new/'app/python_app.py').write_text('''import json,time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=root/'python-migration.json';v=json.loads(p.read_text());v['state']='new_app_started';p.write_text(json.dumps(v))
time.sleep(3)
''')
        shutil.copytree(new/'app',old/'app')
        for name in ('python_updates.py','code_transition.py','source_merge.py'):
            shutil.copy2(ROOT/'desk/tests/fixtures/update211'/name,old/'app/localharness'/name)
        (old/'runtime/bin').mkdir(parents=True);shutil.copy2(sys.executable,old/'runtime/bin/python3')
        files={p.relative_to(new/'app').as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
               for p in (new/'app').rglob('*') if p.is_file()}
        write(new/'source-inputs.json',{'files':files})
        # Old baseline A is genuine for this fixture, never authored L. The
        # shipped view only lets old211 admit the new helper; B remains exact.
        declaration=view(new,old/'tree')
        if mode=='preserve':
            # The selected agent prepares M using its normal isolated hand.
            # No model/worker completion is manufactured by this fixture.
            from localharness import agent_source_update,update_source
            incoming=root/'frozen-agent-incoming';code_transition.baseline(new,incoming)
            identity=source_merge.signature({'files':source_merge.inventory(incoming),'source':str(code),'actor':'selected-agent'})
            directory=old/'.source-updates'/identity
            source_merge.create(old/'tree',code,incoming,directory)
            agent_source_update.request(home,old,code,directory,version)
            status=source_update.command(home)
            source_update.command(home,'resolve',path='upgrade_fixture.py',content=MERGED,
                expected_candidate_sha256=status['candidate_sha256'])
            status=source_update.command(home)
            source_update.command(home,'resolve',path='compatibility_fixture.py',content=LOCAL_FEATURE+HELPER,
                reason='Retain authored feature and incoming shared API',expected_candidate_sha256=status['candidate_sha256'])
            source_update.command(home,'finish')
        before={p.name:p.read_bytes() for p in code.iterdir()}
        old_config=(old/'helene.json').read_bytes()
        profile_before={'memory/llm.json':(home/'memory/llm.json').read_bytes()}
        (home/'memory/owner-data.txt').write_bytes(b'private fixture data')
        profile_before['memory/owner-data.txt']=(home/'memory/owner-data.txt').read_bytes()
        archive=root/('Helene-'+version+'.zip')
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            for p in new.rglob('*'):
                if p.is_file() and '__pycache__' not in p.parts:z.write(p,'Helene/'+p.relative_to(new).as_posix())
        owner_script=root/'legacy-owner.py'
        owner_script.write_text('''import json,sys,time,os,subprocess
from pathlib import Path
from types import SimpleNamespace
root=Path(sys.argv[1]);sys.path.insert(0,str(root/'app'))
from localharness.python_updates import update_install
relay=None
if os.environ.get('HELENE_TEST_RELAY_CUSTODY')=='1':
    from localharness.desktop_supervisor import relay_spec
    from localharness.python_relay import control
    cfg=json.loads((root/'helene.json').read_text());tree=Path(cfg['tree'])
    spec=relay_spec(root/'helene.json',cfg,tree,Path(sys.executable),install_base=root)
    relay=subprocess.Popen(list(spec.argv),cwd=spec.cwd,env=spec.env)
    end=time.monotonic()+15
    while True:
        try:
            assert control(SimpleNamespace(tree=tree),'status')['port']==cfg['relay']['port'];break
        except Exception:
            if time.monotonic()>end:raise
            time.sleep(.05)
    (root.parent/'old-relay-pid.txt').write_text(str(relay.pid))
def close():
    if relay is not None:relay.terminate();relay.wait(10)
    (root.parent/'old-closed.txt').write_text('closed by the existing old updater')
owner=SimpleNamespace(root=root,event=lambda *args:None,browser_mode=True,close=close)
result=update_install(owner,sys.argv[2])
(root.parent/'old-button-result.json').write_text(json.dumps(result))
while True:time.sleep(.1)
''')
        env=dict(os.environ);env.pop('PYTHONPATH',None)
        if coupled:env.update(FIXTURE_RELAY_UPSTREAM=upstream,FIXTURE_RELAY_LIFETIME_LOG=str(root/'relay-lifetime.jsonl'))
        with (root/'old-owner.log').open('wb') as output:
            owner=subprocess.Popen([sys.executable,'-B',str(owner_script),str(old),str(archive)],
                cwd=root,env=env,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            self.assertEqual(owner.wait(timeout=45),0)
            result=read(root/'old-button-result.json')
            self.assertIn('updater_pid',result,result)
            self.assertNotEqual(result.get('state'),'needs_resolution')
            progress=Path(result['staging']).with_name(Path(result['staging']).name+'.progress.json')
            startup_cache=any(Path(result['staging']).rglob('sitecustomize.*.pyc'))
            self.assertTrue(startup_cache,'Incoming interpreter must create startup bytecode before helper admission')
            # A full legacy upstream transition can require hundreds of frozen
            # coverage units. Observe the complete real panel, not just its
            # first workers; the small fake provider does not bypass coverage.
            deadline=time.monotonic()+1800
            choice_made=False
            while time.monotonic()<deadline:
                state=read(progress)
                if state.get('stage')=='choosing_source' and not choice_made:
                    from localharness.update_window import choose_source
                    choose_source(progress,mode);choice_made=True
                if state.get('stage') in {'completed','failed'}:break
                time.sleep(.2)
            self.assertEqual(state.get('stage'),'completed',state)
            self.assertFalse(errors,errors)
            self.assertEqual(read(old/'python-app.json')['version'],version)
            selected=read(old/'helene.json')
            self.assertEqual(selected['tree'],str(home))
            self.assertTrue(choice_made)
            self.assertEqual((Path(selected['code'])/'upgrade_fixture.py').read_text(),INCOMING if mode=='clean' else MERGED)
            self.assertEqual((Path(selected['code'])/'compatibility_fixture.py').read_text(),HELPER if mode=='clean' else LOCAL_FEATURE+HELPER)
            self.assertEqual(before,{p.name:p.read_bytes() for p in code.iterdir()})
            for name,data in profile_before.items():self.assertEqual((home/name).read_bytes(),data)
            self.assertNotEqual(source_merge.inventory(old/'tree'),declaration['view_files'])
            self.assertEqual(bool(requests),mode=='preserve')
            if coupled and mode=='preserve':
                operation=read(source_update.path(home));transport=read(Path(operation['directory'])/'relay-transport.json')
                self.assertTrue(transport['owned']);self.assertEqual(transport['state'],'stopped')
                self.assertNotEqual(transport['pid'],int((root/'old-relay-pid.txt').read_text()))
            self.assertEqual({tuple(r['tools']) for r in requests},{('inspect',)} if mode=='preserve' else set())
            # Wait for the confirmed fixture app and cleanup helper to release
            # their files before exercising the real rollback entry.
            time.sleep(4)
            note=read(old/'python-migration.json');recovery=old.parent/('.helene-recovery-'+note['id'])
            python_source_updater.rollback(old,recovery)
            self.assertEqual(read(old/'python-app.json')['version'],'2.1.1')
            self.assertEqual((old/'helene.json').read_bytes(),old_config)
            for name,data in profile_before.items():self.assertEqual((home/name).read_bytes(),data)
            print('ONE_211_UPDATE_BUTTON_PASS',json.dumps({'update_invocations':1,'helper_admitted':True,
                'actual_upstream_review':True,'semantic_repair':True,'install_and_rollback':True,
                'manual_preparation':False,'ordinary_agent_started':False,'authored_source_preserved':True,
                'profile_preserved':True,'startup_bytecode_present':startup_cache,
                'old_owner_owned_model_transport':coupled,'source_mode':mode,'native_gui':False}),flush=True)
        except BaseException:
            for p in root.glob('helene-update-*.log'):print(p,p.read_text(errors='replace')[-12000:],flush=True)
            for p in root.glob('**/source-review-*.log'):print(p,p.read_text(errors='replace')[-8000:],flush=True)
            print((root/'old-owner.log').read_text(errors='replace')[-8000:],flush=True)
            raise
        finally:
            try:os.killpg(owner.pid,signal.SIGKILL)
            except ProcessLookupError:pass

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
            for directory in (old/'tree',new/'tree'):
                (directory/'compatibility_fixture.py').write_text(HELPER)
            (code/'compatibility_fixture.py').write_text(LOCAL_FEATURE)
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
                                      {'action':'status'},
                                      {'action':'resolve','path':'compatibility_fixture.py',
                                       'content':LOCAL_FEATURE+HELPER,'reason':'Restore shared dependency while retaining authored feature'},
                                      {'action':'finish'}]
                            if count<len(sequence):
                                args={**sequence[count],'expected_input_sha256':digest}
                                if count==3:
                                    status=json.loads([m for m in body['messages'] if m['role']=='tool'][-1]['content'])
                                    args['expected_candidate_sha256']=status['candidate_sha256']
                                call=('merge_source',args)
                            content='Candidate finished; independent review required.'
                        elif tools=={'inspect'}:
                            digest=re.search(r'Subject SHA-256: ([0-9a-f]{64})',system)[1]
                            if count==0:call=('inspect',{'action':'read','path':'resolved/upgrade_fixture.py'})
                            elif count==1:call=('inspect',{'action':'read','path':'resolved/compatibility_fixture.py'})
                            else:
                                returned=[m for m in body['messages'] if m['role']=='tool'][-1]['content']
                                assert LOCAL_FEATURE+HELPER in json.loads(returned)['text']
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
                if os.environ.get('HELENE_TEST_211_BUTTON')=='1':
                    self._one_click(root,old,new,code,home,requests,errors)
                    return
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
                self.assertEqual((directory/'result/compatibility_fixture.py').read_text(),LOCAL_FEATURE+HELPER)
                self.assertEqual(read(directory/'plan.json')['repairs'][0]['path'],'compatibility_fixture.py')
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
                print('REAL_LEGACY_HOST_PASS',json.dumps({'provider_calls':len(requests),'normal_agent_started':False,'old_source_unchanged':True,'canonical_review_validated':True,'semantic_repair':True,'actual_overlay_and_rollback':True,'profile_preserved':True,'native_gui_started':False}),flush=True)
            except BaseException:
                for record in home.glob('memory/.self-review/review-*/request.json'):
                    value=read(record);print('REVIEW_FAILURE',json.dumps({k:value.get(k) for k in ('execution','verdict','gaps','critics','error')}),flush=True)
                for log in new.parent.glob('source-review-*.log'):print(log.read_text(errors='replace')[-16000:],flush=True)
                for log in home.glob('memory/.forge/tasks/**/supervisor.log'):print(str(log),log.read_text(errors='replace')[-4000:],flush=True)
                raise
            finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
