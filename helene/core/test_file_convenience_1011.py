"""Exact file effects, real task routing, multimodal batches and command evidence."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import agent
import editor
import edit_batch
import forge
import workshop


class FileConvenience(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.home=self.base/'workspace';self.home.mkdir()
        self.root=self.base/'task';self.root.mkdir()
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        for obj,name,value in [(workshop,'BASE',self.base),(agent,'BASE',self.base),
                               (forge,'BASE',self.base),(forge,'STATE_DIR',self.base/'memory/.forge'),
                               (forge,'TASKS_DIR',self.base/'memory/.forge/tasks')]:
            self.stack.enter_context(patch.object(obj,name,value))
        forge._save_task(dict(id='code-files',root=str(self.root),scope='self',status='active',
                             goal='file tests',isolation='direct'))
        self.task='code-files'

    def put(self,name,raw):
        path=self.base/name;path.write_bytes(raw);return hashlib.sha256(raw).hexdigest()

    def test_all_counts_exact_bytes_and_reuses_confirmed_version(self):
        version=self.put('workspace/a',b'one\r\none\r\n')
        args=dict(path='workspace/a',old='one',new='two',expected_sha256=version)
        self.assertEqual(editor.edit_result(**args)['status'],'refused')
        result=editor.edit_result(**args,all=True)
        self.assertEqual(result['status'],'confirmed',result);self.assertEqual(result['replacements'],2)
        self.assertEqual((self.home/'a').read_bytes(),b'two\r\ntwo\r\n')
        final=editor.edit_result(path='workspace/a',old='two',new='three',all=True,
                                 expected_sha256=result['after_sha256'])
        self.assertEqual(final['status'],'confirmed',final)
        stale=editor.edit_result(path='workspace/a',old='three',new='bad',all=True,expected_sha256=version)
        self.assertEqual(stale['status'],'refused')
        self.assertEqual((self.home/'a').read_bytes(),b'three\r\nthree\r\n')

    def test_all_refuses_overlaps_non_utf8_empty_and_wrong_type(self):
        for raw,old,all_value in [(b'aaa','aa',True),(b'a\xffa','a',True),(b'aa','',True),(b'aa','a',1)]:
            version=self.put('workspace/a',raw)
            result=editor.edit_result(path='workspace/a',old=old,new='b',all=all_value,expected_sha256=version)
            self.assertEqual(result['status'],'refused',result)
            self.assertEqual((self.home/'a').read_bytes(),raw)

    def test_batch_all_snapshot_and_sequential_counts(self):
        for mode,edits,expected,count in [
            ('snapshot',[dict(old='a',new='b',all=True),dict(old='z',new='q')],b'b b q',3),
            ('sequential',[dict(old='a',new='b',all=True),dict(old='b',new='c',all=True)],b'c c z',4)]:
            version=self.put('workspace/a',b'a a z')
            result=editor.edit_result(action='batch',files=[dict(path='workspace/a',expected_sha256=version,edits=edits)],mode=mode)
            self.assertEqual(result['status'],'confirmed',result)
            self.assertEqual(result['files'][0]['replacements'],count)
            self.assertEqual((self.home/'a').read_bytes(),expected)

    def test_batch_overlap_or_later_preflight_failure_preserves_every_file(self):
        a=self.put('workspace/a',b'a a z');b=self.put('workspace/b',b'aaa')
        result=editor.edit_result(action='batch',files=[
            dict(path='workspace/a',expected_sha256=a,edits=[dict(old='a',new='ok',all=True)]),
            dict(path='workspace/b',expected_sha256=b,edits=[dict(old='aa',new='bad',all=True)])])
        self.assertEqual(result['status'],'refused',result)
        self.assertEqual((self.home/'a').read_bytes(),b'a a z')
        self.assertEqual((self.home/'b').read_bytes(),b'aaa')
        data,error=edit_batch.replacements(b'a a',[dict(old='a',new='b',all=True),dict(old='a a',new='bad')])
        self.assertIsNone(data);self.assertIn('Overlapping',error)

    def test_task_all_uses_same_editor_receipt_and_version(self):
        version=self.put('task/a',b'x x\n')
        result=json.loads(agent.tool_edit('replace',task_id=self.task,path='a',old='x',new='y',
                                         all=True,expected_sha256=version)[6:])
        self.assertEqual(result['status'],'confirmed',result);self.assertEqual(result['replacements'],2)
        self.assertEqual((self.root/'a').read_bytes(),b'y y\n')

    def test_remote_all_is_never_silently_downgraded(self):
        remote=unittest.mock.Mock()
        with patch.object(forge,'_remote_client',return_value=remote):
            result=editor.edit_result(task_id=self.task,path='a',old='a',new='b',all=True)
        self.assertEqual(result['status'],'refused',result)
        remote.workspace_edit_result.assert_not_called();remote.workspace_edit.assert_not_called()

    def test_home_batch_labels_versions_missing_files_and_pixels(self):
        v=self.put('workspace/a',b'text\n')
        original=agent._home_fs_read
        def read(path,*args):
            if path=='workspace/photo':return agent.ToolObservation('photo sha256='+('b'*64),({'pixel':'kept'},))
            if path=='workspace/inaccessible':raise PermissionError('fixture')
            return original(path,*args)
        with patch.object(agent,'_home_fs_read',side_effect=read):
            result=agent.tool_fs_read(paths=['workspace/inaccessible','workspace/a','workspace/missing','workspace/photo'])
        self.assertIsInstance(result,agent.ToolObservation);self.assertEqual(result.images,({'pixel':'kept'},))
        for token in ['--- workspace/a ---',v,'--- workspace/missing ---','Нет файла','--- workspace/photo ---']:
            self.assertIn(token,result.text)
        self.assertIn('--- workspace/inaccessible ---\nRead unavailable: PermissionError',result.text)

    def test_task_file_hands_route_to_task_and_refuse_escape(self):
        v=self.put('task/a.py',b'needle\n');self.put('workspace/a.py',b'wrong home\n')
        result=agent.tool_fs_read(path='a.py',task_id=self.task)
        self.assertIn('needle',result);self.assertIn(v[:16],result);self.assertNotIn('wrong home',result)
        self.assertIn('a.py',agent.tool_fs_ls(task_id=self.task))
        self.assertIn('needle',agent.tool_fs_search('needle',task_id=self.task))
        self.assertNotIn('wrong home',agent.tool_fs_read(path='../workspace/a.py',task_id=self.task))

    def test_batch_validation_happens_before_reads_and_retired_inspect_refuses(self):
        with patch.object(agent,'_home_fs_read') as read,patch.object(forge,'inspect') as inspect:
            for kwargs in [dict(path='a',paths=['b']),dict(paths=[]),dict(paths=['a',3]),
                           dict(paths=['a'],expected_sha256='b'),dict(paths=['a'],start_char=1)]:
                with self.assertRaises(agent.catalog_families.PreflightRefusal):agent.tool_fs_read(**kwargs)
            for action in ['read','list','search']:
                with self.assertRaises(agent.catalog_families.PreflightRefusal):agent.tool_coding_inspect(self.task,action)
            read.assert_not_called();inspect.assert_not_called()

    def test_home_exact_paging_detects_version_conflict(self):
        text='ю'*9000+'\n';v=self.put('workspace/a',text.encode())
        first=json.loads(agent.tool_fs_read('workspace/a',expected_sha256=v))
        self.assertEqual(first['sha256'],v);self.assertGreater(first['next_char'],0)
        second=json.loads(agent.tool_fs_read('workspace/a',start=first['next_start'],
                                            start_char=first['next_char'],expected_sha256=v))
        self.assertEqual(first['text']+second['text'],text)
        self.put('workspace/a',b'changed')
        self.assertIn('Конфликт версии',agent.tool_fs_read('workspace/a',expected_sha256=v))


class CommandEvidence(unittest.TestCase):
    def test_real_python_and_shell_share_launcher_and_changes_tail(self):
        import command_execution
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root=Path(directory)
            subprocess.run(['git','init','-q',str(root)],check=True)
            (root/'a').write_text('old',encoding='utf8')
            subprocess.run(['git','-C',str(root),'add','.'],check=True)
            subprocess.run(['git','-C',str(root),'-c','user.name=T','-c','user.email=t@local','commit','-qm','base'],check=True)
            stack.enter_context(patch.object(agent,'_autocommit_self_edit'))
            stack.enter_context(patch.object(agent,'_pre_shell_dirt',return_value={}))
            stack.enter_context(patch.object(agent.selfgit,'safety_point'))
            for caller,code in [(agent.tool_python,"from pathlib import Path\nPath('a').write_text('python')\nprint('ok')"),
                                (agent.tool_shell,"printf shell > a; printf ok")]:
                with patch.object(command_execution,'launch',wraps=command_execution.launch) as launched:
                    out=caller(code,cwd=directory)
                self.assertEqual(launched.call_count,1);self.assertIn('[command: ok]',out)
                self.assertIn('[changes: '+directory+': a.',out);self.assertTrue(out.endswith('[exit 0]'))

    def test_git_delta_includes_commit_and_deletion_and_does_not_claim_external_coverage(self):
        import command_execution as c
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);subprocess.run(['git','init','-q',directory],check=True)
            (root/'a').write_text('old',encoding='utf8')
            def commit():
                subprocess.run(['git','-C',directory,'add','-A'],check=True)
                subprocess.run(['git','-C',directory,'-c','user.name=T','-c','user.email=t@local','commit','-qm','step'],check=True)
            commit();before=c.workspace_snapshot(root)
            (root/'a').unlink();(root/'b').write_text('new',encoding='utf8');commit()
            result=c.workspace_changes(before,root)
            self.assertIn(': a, b.',result);self.assertIn('outside this Git workspace are not measured',result)
        self.assertIn('not measured',c.workspace_changes(None,'/no-such-workspace'))
