"""Current edit receipts retain byte/CAS truth independently of display grammar."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
import editor
import forge
import frame_language as fl
import hands
import workshop

RAW='Literal Русский 中文 {v1} <script>inert</script>'
sha=lambda raw:hashlib.sha256(raw).hexdigest()

class ForgeEditLanguage(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.base=Path(temp.name)
        self.root=self.base/'project';self.root.mkdir();self.path=self.root/'a {v1} 中文.txt'
        for obj,name,value in [(forge,'BASE',self.base),(forge,'REPO',self.base/'self'),(forge,'STATE_DIR',self.base/'state'),(forge,'TASKS_DIR',self.base/'state/tasks'),(forge,'_UPSTREAMS',self.base/'state/upstreams.json'),(workshop,'BASE',self.base)]:self.enterContext(patch.object(obj,name,value))
        self.enterContext(patch.object(forge.forge_learning,'recall',return_value=[]))
        self.task=forge.start(RAW,target=str(self.root),isolation='direct').task_id
    def settings(self,ui,language):return patch.object(fl,'settings',return_value={'lang':ui,'agent':{'frame_language':language}})
    def edit(self,**kwargs):return editor.edit_result(task_id=self.task,path=self.path.name,**kwargs)

    def test_actual_replace_receipts_keep_full_hashes_counts_and_next_edit16axes(self):
        original=('old\r\n'+RAW+' old\r\n').encode('utf8');expected=original.replace(b'old',b'new')
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                self.path.write_bytes(original)
                with self.settings(ui,language):
                    result=self.edit(action='replace',old='old',new='new',all=True,expected_sha256=sha(original))
                    self.assertEqual(result['status'],'confirmed');self.assertEqual(result['before_sha256'],sha(original));self.assertEqual(result['after_sha256'],sha(expected));self.assertEqual(result['replacements'],2);self.assertEqual(self.path.read_bytes(),expected)
                    following=self.edit(action='replace',old=RAW,new=RAW+' next',expected_sha256=result['after_sha256'])
                    self.assertEqual(following['status'],'confirmed');self.assertEqual(following['after_sha256'],sha(expected.replace(RAW.encode(),(RAW+' next').encode())))

    def test_display_grammar_cannot_change_current_status_or_drop_hashes(self):
        original=RAW.encode();self.path.write_bytes(original);original_text=fl.text
        def captions(key,**params):return 'Caption with no parsable receipt: '+RAW if key=='forge.edit.17' else original_text(key,**params)
        with patch.object(fl,'text',side_effect=captions):result=self.edit(action='write',content='changed',expected_sha256=sha(original))
        self.assertEqual(result['message'],'Caption with no parsable receipt: '+RAW);self.assertEqual(result['status'],'confirmed');self.assertEqual(result['before_sha256'],sha(original));self.assertEqual(result['after_sha256'],sha(b'changed'))

    def test_actual_stale_and_absent_paths_refuse_before_publication16axes(self):
        original=RAW.encode();self.path.write_bytes(original)
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with self.settings(ui,language),patch.object(hands,'publish',wraps=hands.publish) as publish:
                    result=self.edit(action='write',content='bad',expected_sha256='0'*64);self.assertEqual(result['status'],'refused');publish.assert_not_called();self.assertEqual(self.path.read_bytes(),original)
                    result=editor.edit_result(task_id=self.task,action='replace',path='absent',old='old',new='bad')
                    self.assertEqual(result['status'],'refused');self.assertEqual(result['message'],fl.text('forge.edit.15',v1='absent'));publish.assert_not_called();self.assertFalse((self.root/'absent').exists())

    def test_actual_readback_loss_is_unknown_after_one_publication(self):
        for language in fl.LANGUAGES:
            self.path.write_bytes(b'old')
            with fl.use(language),patch.object(hands,'_digest_of',side_effect=OSError('fixture readback loss')),patch.object(hands,'publish',wraps=hands.publish) as publish:
                result=self.edit(action='write',content=RAW)
                publish.assert_called_once();self.assertEqual(result['status'],'in_doubt');self.assertIsNone(result['after_sha256']);self.assertEqual(self.path.read_bytes(),RAW.encode())

    def test_takeover_and_release_error_downgrade_actual_written_bytes_in_all_languages(self):
        real_lock=workshop.mutation_lock
        for lost in (True,False):
            @contextmanager
            def boundary():
                with real_lock() as lock:
                    yield SimpleNamespace(lost_to_takeover=lost)
                if not lost:raise OSError('fixture release error')
            for language in fl.LANGUAGES:
                self.path.write_bytes(b'old')
                with fl.use(language),patch.object(workshop,'mutation_lock',boundary),patch.object(hands,'publish',wraps=hands.publish) as publish:
                    result=self.edit(action='write',content=RAW)
                    publish.assert_called_once();self.assertEqual(result['status'],'in_doubt');self.assertEqual(self.path.read_bytes(),RAW.encode())
                    self.assertIn(fl.text('forge.edit.22') if lost else fl.text('forge.edit.24',v1='OSError',v3='').split(':',1)[0],result['message'])

    def test_pin_survives_actual_publication_before_response_formatting(self):
        self.path.write_bytes(b'old');choice={'language':'en'};original=hands.publish
        def settings():return {'lang':'zh','agent':{'frame_language':choice['language']}}
        def publish(*args,**kwargs):value=original(*args,**kwargs);choice['language']='fr';return value
        with patch.object(fl,'settings',side_effect=settings),patch.object(hands,'publish',side_effect=publish) as effect:
            result=self.edit(action='write',content=RAW);effect.assert_called_once()
        with fl.use('en'):self.assertEqual(result['message'],fl.text('forge.edit.17',v0='write',v2=self.path.name,v4=sha(b'old')[:16],v6=sha(RAW.encode()),v8=len(RAW.encode()),v10=''))

    @unittest.skipIf(sys.platform=='win32','Git child qualification belongs to the isolated Linux gate')
    def test_actual_patch_is_ok_without_readback_claim_and_invalid_patch_is_untouched(self):
        target=self.root/'patch.txt';subprocess.run(['git','init','-q',str(self.root)],check=True)
        change='--- a/patch.txt\n+++ b/patch.txt\n@@ -1 +1 @@\n-old\n+new\n'
        for language in fl.LANGUAGES:
            target.write_bytes(b'old\n')
            with fl.use(language),patch.object(forge,'_run',wraps=forge._run) as run:
                result=editor.edit_result(task_id=self.task,action='patch',patch=change);self.assertEqual(result['status'],'ok');self.assertIsNone(result['after_sha256']);self.assertEqual(target.read_bytes(),b'new\n')
                applies=[call for call in run.call_args_list if call.args[0][:2]==['git','apply']];self.assertEqual(len(applies),2)
                result=editor.edit_result(task_id=self.task,action='patch',patch=change);self.assertEqual(result['status'],'refused');self.assertEqual(target.read_bytes(),b'new\n')
                applies=[call for call in run.call_args_list if call.args[0][:2]==['git','apply']];self.assertEqual(len(applies),3)

    def test_apply_failure_after_check_is_unknown_and_never_retried(self):
        for language in fl.LANGUAGES:
            self.path.write_bytes(b'old')
            def run(argv,**kwargs):
                if '--check' in argv:return SimpleNamespace(returncode=0,stderr='',stdout='')
                self.path.write_bytes(RAW.encode());return SimpleNamespace(returncode=1,stderr=RAW,stdout='')
            with fl.use(language),patch.object(forge,'_run',side_effect=run) as effect:
                result=editor.edit_result(task_id=self.task,action='patch',patch='fixture patch');self.assertEqual(effect.call_count,2);self.assertEqual(result['status'],'in_doubt');self.assertEqual(result['message'],fl.text('forge.edit.10')+RAW);self.assertEqual(self.path.read_bytes(),RAW.encode())

    def test_remote_raw_text_is_unknown_and_structured_result_keeps_backend_evidence(self):
        task=forge.get(self.task);task['scope']='host';forge._save_task(task)
        for language in fl.LANGUAGES:
            backend=Mock();backend.workspace_edit.return_value='Patch применён. '+RAW;backend.workspace_edit_result.return_value={'ok':True,'type':'result','text':RAW,'after':'a'*64}
            with fl.use(language),patch.object(forge,'_remote_client',return_value=backend):
                raw=forge.edit(self.task,'write',path='remote',content=RAW);self.assertEqual(str(raw),'Patch применён. '+RAW);self.assertEqual(forge._edit_outcome(raw),'in_doubt');backend.workspace_edit.assert_called_once()
                result=editor.edit_result(task_id=self.task,action='write',path='remote',content=RAW);self.assertEqual(result['status'],'ok');self.assertEqual(result['message'],RAW);self.assertEqual(result['after_sha256'],'a'*64);self.assertEqual(result['backend_result'],backend.workspace_edit_result.return_value);backend.workspace_edit_result.assert_called_once()

if __name__=='__main__':unittest.main()
