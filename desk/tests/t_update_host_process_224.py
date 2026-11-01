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
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'desk'))
from localharness import code_transition, source_merge, source_update, source_update_host, python_source_updater,authored_baseline
from localharness.package_notice import read,write
from localharness.app_migration import code_path

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
        # A modern delivery can contain an older controller/Forge capability.
        # Its exact shipped app/tree A must still predate authored L.
        code_transition.seal(old);authored_baseline.seal(old)
        code_transition.seal(new);authored_baseline.seal(new)
        (code/'upgrade_fixture.py').write_text(MINE)
        (code/'compatibility_fixture.py').write_text(LOCAL_FEATURE)
        from update_verification_fixture import bind
        bind(root,new,version)
        passport=read(new/'python-package-files.json')
        passport['files']['runtime/bin/python3']=hashlib.sha256(launcher.read_bytes()).hexdigest()
        write(new/'python-package-files.json',passport)
        # Old baseline A is genuine for this fixture, never authored L. The
        # shipped view only lets old211 admit the new helper; B remains exact.
        declaration=view(new,old/'tree',sealed=True)
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
        before=source_merge.inventory(code)
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
            selected_code=code_path(old/'helene.json',selected)
            self.assertEqual((selected_code/'upgrade_fixture.py').read_text(),INCOMING if mode=='clean' else MERGED)
            self.assertEqual((selected_code/'compatibility_fixture.py').read_text(),HELPER if mode=='clean' else LOCAL_FEATURE+HELPER)
            self.assertEqual(before,source_merge.inventory(code))
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
        self._run_host_fixture()

    def _run_host_fixture(self, application=None):
        with tempfile.TemporaryDirectory(prefix='update-host-224-') as temporary:
            root=Path(temporary);old=root/'old';new=root/'.helene-update-fixture/Helene'
            code=root/'authored';home=root/'profile'
            button=os.environ.get('HELENE_TEST_211_BUTTON')=='1' and application is None
            for p in (old/'tree',code,home/'memory',new/'app',new/'tree',new/'runtime/bin'):p.mkdir(parents=True)
            # Old Forge cannot coordinate review. The selected code must never
            # become an executable import path in the incoming host or workers.
            legacy='raise RuntimeError("AUTHORED_FORGE_MUST_NOT_EXECUTE")\n'
            # Supply the real incoming runtime/Forge closure. Its protocol gate
            # uses the tiny declared verification fixture below; copying the
            # entire product test suite here would execute an undeclared nested
            # full battery with different dependencies. The release battery
            # independently runs the complete, canonical product inventory.
            shutil.copytree(ROOT/'helene/core',new/'tree',dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns('__pycache__','.git','test*.py','conftest.py','praxis_test.py'))
            from update_verification_fixture import BOOTSTRAP
            (new/'tree/praxis_test.py').write_text(BOOTSTRAP,encoding='utf8')
            # A real previous release already has the unchanged runtime/core
            # closure. Keep that shared base in A/L, with a deliberately
            # unavailable old Forge and the authored overlapping changes below.
            # Treating every incoming dependency as newly added creates hundreds
            # of unrelated review units and tests a different transition.
            for directory in (old/'tree',code):
                shutil.copytree(new/'tree',directory,dirs_exist_ok=True)
                (directory/'forge.py').write_text(legacy)
                (directory/'upgrade_fixture.py').write_text(BASE)
            (new/'tree/upgrade_fixture.py').write_text(INCOMING)
            for directory in (old/'tree',new/'tree'):
                (directory/'compatibility_fixture.py').write_text(HELPER)
            (code/'compatibility_fixture.py').write_text(HELPER)
            shutil.copytree(ROOT/'desk/localharness',new/'app/localharness',ignore=shutil.ignore_patterns('__pycache__'))
            for name in ('python_migrate.py','python_app.py'):
                shutil.copy2(ROOT/'desk'/name,new/'app'/name)
            for name,values in (application or {}).get('files',{}).items():
                target=new/'app'/name;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(values['incoming'],encoding='utf8')
            shutil.copy2(sys.executable,new/'runtime/bin/python3')
            write(old/'python-app.json',{'version':'2.1.1','delivery':'portable'})
            write(new/'python-app.json',{'version':'2.2.4','delivery':'portable'})
            write(old/'helene.json',{'tree':str(home),'code':str(code),'runner':'','setup_complete':True,
                                      'self_review':{'max_parallel':8}})
            from localharness import update_source
            if not button:
                shutil.copytree(new/'app',old/'app')
                for name,values in (application or {}).get('files',{}).items():
                    (old/'app'/name).write_text(values['base'],encoding='utf8')
                # Genuine A and B, including application source, precede L and
                # admission. Missing old Forge capability does not imply an
                # ancient installation with no authored-update baseline.
                code_transition.seal(old);authored_baseline.seal(old)
                code_transition.seal(new);authored_baseline.seal(new)
                (code/'upgrade_fixture.py').write_text(MINE)
                (code/'compatibility_fixture.py').write_text(LOCAL_FEATURE)
                for name,values in (application or {}).get('files',{}).items():
                    (old/'app'/name).write_text(values['local'],encoding='utf8')
                update_source.select(old,new,'preserve',by='updater',require_review=True,resolution_owner='incoming')
            files={p.relative_to(new/'app').as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in (new/'app').rglob('*') if p.is_file()}
            write(new/'source-inputs.json',{'files':files})
            from update_verification_fixture import bind
            bind(root,new,'2.2.4',desk_test=(application or {}).get('desk_test'))
            passport=read(new/'python-package-files.json')
            passport['files']['runtime/bin/python3']=hashlib.sha256((new/'runtime/bin/python3').read_bytes()).hexdigest()
            write(new/'python-package-files.json',passport)
            requests=[];errors=[];merger_runs=[0]
            old_app_before={p.relative_to(old/'app').as_posix():p.read_bytes()
                            for p in (old/'app').rglob('*') if p.is_file()}
            baseline_names=('app-upstream.zip','app-upstream.json','source-update-baseline.json')
            incoming_baseline={name:(new/name).read_bytes() for name in baseline_names} if application else {}
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
                            if count==0:merger_runs[0]+=1
                            if application and count==0:print('APP_PROTOCOL_MERGER',merger_runs[0],flush=True)
                            digest=re.search(r'Input SHA256=([0-9a-f]{64})',system)[1]
                            sequence=[{'action':'read','path':'upgrade_fixture.py'},
                                      {'action':'resolve','path':'upgrade_fixture.py','content':MERGED},
                                      {'action':'status'},
                                      {'action':'resolve','path':'compatibility_fixture.py',
                                       'content':LOCAL_FEATURE+HELPER,'reason':'Restore shared dependency while retaining authored feature'},
                                      {'action':'finish'}]
                            if application:sequence=application['sequence'](merger_runs[0])
                            if count<len(sequence):
                                args={**sequence[count],'expected_input_sha256':digest}
                                if args.get('action')=='resolve':
                                    statuses=[json.loads(m['content']) for m in body['messages'] if m['role']=='tool']
                                    statuses=[value for value in statuses if 'candidate_sha256' in value]
                                    if args.get('path','').startswith('app/'):
                                        args['expected_composition_sha256']=statuses[-1]['composition_sha256']
                                    elif args.get('reason'):args['expected_candidate_sha256']=statuses[-1]['candidate_sha256']
                                call=('merge_source',args)
                            content='Candidate finished; independent review required.'
                        elif tools=={'inspect'}:
                            digest=re.search(r'Subject SHA-256: ([0-9a-f]{64})',system)[1]
                            findings=[]
                            expected=[('resolved/upgrade_fixture.py',MERGED),
                                      ('resolved/compatibility_fixture.py',LOCAL_FEATURE+HELPER)]
                            expected.extend((application or {}).get('expected',{}).items())
                            if count<len(expected):call=('inspect',{'action':'read','path':expected[count][0]})
                            else:
                                observed={}
                                for message in body['messages']:
                                    if message['role']=='tool':
                                        returned=json.loads(message['content'])
                                        observed[returned.get('path')]=returned.get('text')
                                for path,wanted in expected:
                                    if observed.get(path)!=wanted:
                                        findings.append({'path':path,'scenario':'preserve authored behavior and incoming API',
                                                         'detail':'Candidate differs from the fixture intent: '+wanted})
                            content=json.dumps({'subject_sha256':digest,'verdict':'findings' if findings else 'clear',
                                                'summary':'Deterministic fixture only','findings':findings,'gaps':[]})
                            if application and not call:print('APP_PROTOCOL_REVIEW','findings' if findings else 'clear',flush=True)
                        else:raise AssertionError('Unexpected general-agent tools: '+str(tools))
                        requests.append({'tools':sorted(tools),'path':self.path,'tool':call[0] if call else None,
                            'attempt':merger_runs[0] if tools=={'merge_source'} else None,
                            'action':call[1].get('action') if call else None,
                            'logical':call[1].get('path') if call else None})
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
                if button:
                    self._one_click(root,old,new,code,home,requests,errors)
                    return
                result=code_transition.preflight(old,new)
                self.assertEqual(result['plans'][0]['state'],'needs_attention')
                directory=Path(result['plans'][0]['directory'])
                source_merge.resolve(directory,'upgrade_fixture.py',side='incoming')
                source_merge.finish(directory,exe=Path(shutil.which('git')))
                operation=read(directory/'pipeline.json');operation['created']=time.time()-7200;write(directory/'pipeline.json',operation)
                before=source_merge.inventory(code)
                old_config=(old/'helene.json').read_bytes()
                (home/'memory/owner-data.txt').write_bytes(b'private fixture: preserve exact bytes')
                profile_before={name:(home/name).read_bytes() for name in ('memory/llm.json','memory/owner-data.txt')}
                # The real legacy entry prepares AFTER old-owner exit, with no
                # capable running agent. Preparation includes the actual host,
                # worker processes, independent panel and recoverable overlay.
                try:
                    # This test explicitly exercises a real SDK against its own
                    # loopback HTTP fixture. PRAXIS_TEST intentionally disables
                    # SDK clients; retain the outer network-none sandbox and
                    # synthetic config, but don't inherit that different seam.
                    with patch.dict(os.environ):
                        os.environ.pop('PRAXIS_TEST',None)
                        prepared=python_source_updater.prepare_install(old,new)
                except Exception:
                    # Keep causal child diagnostics when TemporaryDirectory is
                    # retired; this fixture contains only synthetic local input.
                    for output in root.rglob('*.output'):
                        if 'battery' in str(output):
                            print('CHILD BATTERY',output.name,output.read_text(errors='replace')[-5000:],file=sys.stderr)
                    raise
                self.assertFalse(errors,errors)
                accepted=source_update.validate_ready(directory)
                self.assertEqual(accepted['state'],'ready_to_apply')
                self.assertEqual((directory/'result/upgrade_fixture.py').read_text(),MERGED)
                self.assertEqual((directory/'result/compatibility_fixture.py').read_text(),LOCAL_FEATURE+HELPER)
                self.assertEqual(read(directory/'plan.json')['repairs'][0]['path'],'compatibility_fixture.py')
                self.assertEqual(before,source_merge.inventory(code))
                self.assertEqual(read(old/'python-app.json')['version'],'2.1.1')
                self.assertEqual({tuple(r['tools']) for r in requests},{('merge_source',),('inspect',)})
                if application:
                    self.assertEqual(len(accepted['attempts']),3)
                    self.assertEqual(len(accepted.get('failed_batteries',[])),1)
                    failed=accepted['failed_batteries'][0]['findings']
                    self.assertTrue(any('test_authored_identity_uses_new_upstream_contract' in row['case_id']
                        and 'authored/v2-broken' in row['detail'] for row in failed))
                    repairs=[r for r in requests if r['action']=='resolve' and r['attempt'] in (2,3)]
                    self.assertTrue(repairs)
                    self.assertTrue(all(r['logical'].startswith('app/') for r in repairs))
                    reports=[read(p) for p in (directory/'test-battery').glob('*/report.json')]
                    self.assertEqual(len(reports),3)
                    self.assertEqual(sum(r.get('full_pass') is False for r in reports),1)
                    self.assertEqual(sum(r.get('full_pass') is True for r in reports),2)
                    self.assertEqual(len({r['binding']['candidate_sha256'] for r in reports}),1)
                    self.assertEqual(len({r['binding']['composition_sha256'] for r in reports}),3)
                    reviews=[read(p) for p in home.glob('memory/.source-update-reviews/review-*/request.json')]
                    self.assertIn('findings',{r.get('verdict') for r in reviews})
                    self.assertIn('clear',{r.get('verdict') for r in reviews})
                    self.assertGreaterEqual(len({r.get('subject_sha256') for r in reviews}),2)
                    self.assertEqual(old_app_before,{p.relative_to(old/'app').as_posix():p.read_bytes()
                        for p in (old/'app').rglob('*') if p.is_file() and '__pycache__' not in p.parts})
                    for name,data in incoming_baseline.items():self.assertEqual((new/name).read_bytes(),data)
                    for name,values in application['files'].items():
                        self.assertEqual((directory/'compound/app'/name).read_text(),values['merged'])
                note,recovery=python_source_updater.install(old,new)
                self.assertEqual(str(recovery),prepared['recovery'])
                selected=read(old/'helene.json')
                self.assertEqual(read(old/'python-app.json')['version'],'2.2.4')
                self.assertEqual((code_path(old/'helene.json',selected)/'upgrade_fixture.py').read_text(),MERGED)
                self.assertEqual(selected['tree'],str(home))
                if application:
                    for name,values in application['files'].items():
                        self.assertEqual((old/'app'/name).read_text(),values['merged'])
                    for name,data in incoming_baseline.items():self.assertEqual((old/name).read_bytes(),data)
                for name,data in profile_before.items():self.assertEqual((home/name).read_bytes(),data)
                self.assertEqual(before,source_merge.inventory(code))
                python_source_updater.rollback(old,recovery)
                self.assertEqual(read(old/'python-app.json')['version'],'2.1.1')
                self.assertEqual((old/'helene.json').read_bytes(),old_config)
                self.assertEqual(before,source_merge.inventory(code))
                if application:
                    self.assertEqual(old_app_before,{p.relative_to(old/'app').as_posix():p.read_bytes()
                        for p in (old/'app').rglob('*') if p.is_file() and '__pycache__' not in p.parts})
                    for name,values in application['files'].items():
                        self.assertEqual((old/'app'/name).read_text(),values['local'])
                    print('REAL_APP_PROTOCOL_PASS',json.dumps({'merger_attempts':merger_runs[0],
                        'battery_failed_then_repaired':True,'review_rejected_then_repaired':True,
                        'corrections_app_only':True,'pristine_B_unchanged':True,
                        'actual_install_rollback':True,'scope':'synthetic protocol fixture; not product approval'}),flush=True)
                print('REAL_LEGACY_HOST_PASS',json.dumps({'provider_calls':len(requests),'normal_agent_started':False,'old_source_unchanged':True,'canonical_review_validated':True,'semantic_repair':True,'actual_overlay_and_rollback':True,'profile_preserved':True,'native_gui_started':False}),flush=True)
            except BaseException:
                if application:
                    print('APP_PROVIDER_FLOW',json.dumps(requests),flush=True)
                for record in home.glob('memory/.source-update-reviews/review-*/request.json'):
                    value=read(record);print('REVIEW_FAILURE',json.dumps({k:value.get(k) for k in ('execution','verdict','gaps','critics','error')}),flush=True)
                for log in new.parent.glob('source-review-*.log'):print(log.read_text(errors='replace')[-16000:],flush=True)
                for log in home.glob('memory/.forge/tasks/**/supervisor.log'):print(str(log),log.read_text(errors='replace')[-4000:],flush=True)
                raise
            finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
