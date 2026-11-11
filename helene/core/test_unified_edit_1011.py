"""Public editor routing and real foreground Python custody, not a mocked lock bypass."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import agent
import editor
import forge
import forge_script
import forge_worker
import hands
import workshop
from pyhands import praxis_hands as h


class Contract(unittest.TestCase):
    def test_remote_nonterminal_and_lost_reply_are_unknown(self):
        for response in (None,{'ok':True,'type':'accepted'}, {'ok':True,'type':'cancelled'},
                         {'ok':False,'code':'timeout','request_id':'request-a'},
                         {'ok':False,'code':'transport'}, {'ok':False,'error':'apply failed'},
                         {'ok':True,'after_error':'readback failed'}):
            self.assertEqual(editor.remote_result(response)['status'],'in_doubt')
        self.assertEqual(editor.remote_result({'ok':False,'code':'unavailable'})['status'],'refused')

    def test_context_is_hash_bound_on_all_native_platforms(self):
        env,binding=forge_script.issue('code-x','proc-x','',base='/tmp/example')
        context=json.loads(env[forge_script.ENV])
        self.assertEqual(hashlib.sha256(context['token'].encode()).hexdigest(),binding['token_sha256'])
        self.assertNotIn(context['token'],json.dumps(binding))
        with patch.object(forge_script.sys,'platform','win32'):
            env,binding=forge_script.issue('code-x','proc-x','',base='/tmp/example')
        self.assertTrue(forge_script.ENV in env)
        self.assertEqual(hashlib.sha256(json.loads(env[forge_script.ENV])['token'].encode()).hexdigest(), binding['token_sha256'])

    def test_catalog_and_k_have_one_editor(self):
        ctx=agent.ChannelContext(chat_id='42',room_id='42',is_dm=True,owner=True,known=True)
        names={t.get('name') for t in agent.catalog_tools_for(ctx)}
        self.assertIn('edit',names)
        for old in ('fs_edit','coding_edit'):
            self.assertNotIn(old,names)
            self.assertNotIn(old,{t.get('name') for t in agent.TOOLS})
            self.assertNotIn(old,agent._hands_contract_text())
        self.assertIn('task_id',agent._hands_contract_text())
        self.assertEqual(editor.schema(True),forge_worker.EDIT_TOOL['input_schema'])
        self.assertNotIn('task_id',editor.schema(True)['properties'])

    def test_home_proposal_and_write_options_reach_original_guard(self):
        receipt=dict(hands.outcome('confirmed','yes'),after_sha256='a'*64)
        with patch.object(workshop,'fs_write_result',return_value=receipt) as write:
            self.assertEqual(editor.edit_result(action='write',path='core.py',content='x',proposal_id='p',overwrite=True,force=True,expected_sha256='v'),receipt)
        write.assert_called_once_with('core.py','x','p',True,True,'v')

    def test_invalid_combination_never_dispatches(self):
        with patch.object(forge,'edit') as f,patch.object(workshop,'fs_edit_result') as w:
            for args in ({'action':'patch','patch':'x'},{'action':'replace','files':[]},
                         {'action':'batch'},{'action':'batch','files':[],'path':'x'},
                         {'task_id':'x','proposal_id':'p'},{'action':'invented'}):
                self.assertEqual(editor.edit_result(**args)['status'],'refused')
            f.assert_not_called();w.assert_not_called()

    def test_worker_cannot_invent_identity(self):
        request={'task_id':'x','role':'worker','_verified_request_path':'/verified'}
        with patch.object(editor,'edit',return_value='ok') as edit:
            for key in ('task_id','_script_context','root','_parent_request_path'):
                self.assertIn('refused',forge_worker._dispatch(request,'edit',{key:'fake'}))
            edit.assert_not_called()
            self.assertEqual(forge_worker._dispatch(request,'edit',{'action':'patch','patch':'p'}),'ok')
            edit.assert_called_once_with(task_id='x',_parent_request_path='/verified',action='patch',patch='p')


@unittest.skipUnless(sys.platform.startswith('linux'),'Requires actual Linux command custody')
class RealCommand(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'project';self.root.mkdir()
        (self.root/'a').write_bytes(b'alpha\r\n');(self.root/'nested').mkdir()
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        subprocess.run(['git','-C',str(self.root),'add','.'],check=True)
        subprocess.run(['git','-C',str(self.root),'-c','user.name=Test','-c','user.email=test@local','commit','-qm','initial'],check=True)
        for obj,name,value in ((forge,'BASE',self.base),(forge,'STATE_DIR',self.base/'memory/.forge'),
                               (forge,'TASKS_DIR',self.base/'memory/.forge/tasks'),(workshop,'BASE',self.base)):
            p=patch.object(obj,name,value);p.start();self.addCleanup(p.stop)
        env=patch.dict(os.environ,{'PRAXIS_BASE':str(self.base),'PRAXIS_HANDS_ROOT':str(Path(__file__).parent)})
        env.start();self.addCleanup(env.stop)
        self.task=re.search(r'code-[a-f0-9]+',forge.start('test editor',target=str(self.root),isolation='direct')).group()

    def command(self,body,may_stop=False,**kwargs):
        code='import json,os,pathlib,praxis_hands as h\n'+body+'\nprint("SCRIPT-PASS")\n'
        output=forge.run(self.task,shlex.quote(sys.executable)+' -B -c '+shlex.quote(code),timeout=30,**kwargs)
        if may_stop and 'SCRIPT-PASS' not in output:
            # A real parent Stop may terminate the process before Python catches
            # the refusal. Both outcomes must preserve the last admitted bytes.
            self.assertIn('[command: stopped;',output,output)
        else:
            self.assertIn('SCRIPT-PASS',output,output)
        self.assertNotIn('Traceback',output,output)
        return output

    def test_foreground_script_write_replace_batch_stale_and_root(self):
        self.command('''
text,v=h.read('a'); assert text=='alpha\\r\\n'
h.write('new','created')
v2=h.edit('a','alpha','beta',expected_sha256=v)
try:h.edit('a','beta','bad',expected_sha256=v)
except h.Refused as exc:assert exc.result['status']=='refused'
else:raise AssertionError('stale accepted')
receipt=h.edit(files=[{'path':'a','expected_sha256':v2,'edits':[{'old':'beta','new':'gamma'}]}])
assert receipt['status']=='confirmed'
try:h.edit('a','gamma','bad',task_id='code-other')
except h.Refused:pass
else:raise AssertionError('wrong task accepted')
''',cwd='nested')
        self.assertEqual((self.root/'a').read_bytes(),b'gamma\r\n')
        self.assertEqual((self.root/'new').read_text(),'created')

    def test_explicit_python_task_and_public_patch(self):
        _,v=h.read('a',task_id=self.task)
        result=h.edit('a','alpha','beta',expected_sha256=v,task_id=self.task)
        self.assertEqual(result,hashlib.sha256(b'beta\r\n').hexdigest())
        patch_text='diff --git a/new b/new\nnew file mode 100644\n--- /dev/null\n+++ b/new\n@@ -0,0 +1 @@\n+content\n'
        reply=json.loads(editor.edit(task_id=self.task,action='patch',patch=patch_text)[6:])
        self.assertEqual(reply['status'],'ok',reply)
        self.assertEqual((self.root/'new').read_text(),'content\n')

    def test_remote_adapters_keep_structured_success_and_lost_reply(self):
        import serverd_client,body_client
        for client in (serverd_client,body_client):
            for action in ('patch','replace','write'):
                reply={'ok':True,'text':'M a','before':'a'*16,'after':'b'*16,'request_id':'remote-id'}
                with patch.object(forge,'_remote_client',return_value=client),patch.object(client,'call',return_value=reply) as sent:
                    result=editor.edit_result(task_id=self.task,action=action,path='a',old='alpha',new='beta',patch='diff')
                self.assertEqual(result['status'],'ok',result)
                self.assertEqual(result['backend_result'],reply)
                self.assertEqual(sent.call_count,1)
            reply={'ok':False,'code':'timeout','request_id':'lost-id'}
            with patch.object(forge,'_remote_client',return_value=client),patch.object(client,'call',return_value=reply) as sent:
                result=editor.edit_result(task_id=self.task,action='patch',patch='diff')
            self.assertEqual(result['status'],'in_doubt')
            self.assertEqual(result['backend_result']['request_id'],'lost-id')
            self.assertEqual(sent.call_count,1)
        self.assertEqual((self.root/'a').read_bytes(),b'alpha\r\n')

    def test_inherited_context_is_not_reusable_after_exit(self):
        self.command("pathlib.Path('context').write_text(os.environ['PRAXIS_EDIT_CONTEXT'])")
        context=json.loads((self.root/'context').read_text())
        with patch.dict(os.environ,{forge_script.ENV:json.dumps(context)}):
            with self.assertRaises(h.Refused):h.edit('a','alpha','bad')
        self.assertEqual((self.root/'a').read_bytes(),b'alpha\r\n')

    def test_own_command_cannot_bypass_stop_or_other_writer(self):
        self.command('''
h._workshop()
import forge,forge_script
c=forge_script.inherited();task=c['task_id']
root=forge._task_dir(task)
for path,data in [(root/'control.json',{'action':'pause'}),
                  (root/'processes/proc-other/request.json',{'id':'proc-other','task_id':task,'kind':'process','supervisor_pid':os.getpid()})]:
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data))
    try:h.edit('a','alpha','bad')
    except h.Refused:pass
    else:raise AssertionError('fence bypassed')
    path.unlink()
''')
        self.assertEqual((self.root/'a').read_bytes(),b'alpha\r\n')

    def test_bad_token_and_non_descendant_refused_inside_live_command(self):
        self.command('''
h._workshop()
import forge,forge_script,editor,subprocess,sys
c=forge_script.inherited();bad=dict(c,token='fake')
assert editor.edit_result(task_id=c['task_id'],path='a',old='alpha',new='bad',_script_context=bad)['status']=='refused'
# A live but unrelated PID cannot stand in for this command's bound custodian.
req=forge._unit_dir(c['task_id'],'processes',c['unit_id'])/'request.json'
data=json.loads(req.read_text());orig=dict(data)
other=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'])
data.update(supervisor_pid=other.pid,supervisor_started_at=forge._proc_started_at(other.pid))
req.write_text(json.dumps(data))
try:assert editor.edit_result(task_id=c['task_id'],path='a',old='alpha',new='bad',_script_context=c)['status']=='refused'
finally:
    req.write_text(json.dumps(orig));other.terminate();other.wait()
''')

    def test_parent_worker_context_and_parent_stop(self):
        directory=forge._unit_dir(self.task,'agents','agent-parent');directory.mkdir(parents=True)
        request=directory/'request.json'
        request.write_text(json.dumps({'id':'agent-parent','task_id':self.task,'role':'worker'}))
        self.command('''
h._workshop()
import forge,forge_script
c=forge_script.inherited();parent,unit=forge_script.validate(forge,c['task_id'],c)
h.edit('a','alpha','beta')
p=pathlib.Path(parent).parent/'stop.json';p.write_text('{}')
try:
    try:h.edit('a','beta','bad')
    except h.Refused:pass
    else:raise AssertionError('parent Stop bypassed')
finally:p.unlink()
''',_parent_request_path=str(request),may_stop=True)
        self.assertEqual((self.root/'a').read_bytes(),b'beta\r\n')


if __name__=='__main__':unittest.main()
