"""Owned run captions preserve command evidence, Git effects and termination facts."""
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import command_execution
import forge
import frame_language as fl
import tool_report
import workshop

RAW='literal Русский 中文 {v1} <script>inert</script>'


class ForgeRunLanguage(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.base=Path(temp.name)
        self.root=self.base/'project';self.root.mkdir();self.path=self.root/'authored.txt';self.path.write_bytes(b'initial\r\n')
        self.git=shutil.which('git');self.assertIsNotNone(self.git)
        self.git_run('init','-q')
        self.git_run('add','authored.txt')
        self.git_run('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','initial')
        for obj,name,value in [(forge,'BASE',self.base),(forge,'REPO',self.base/'self'),(forge,'STATE_DIR',self.base/'state'),(forge,'TASKS_DIR',self.base/'state/tasks'),(forge,'_UPSTREAMS',self.base/'state/upstreams.json'),(workshop,'BASE',self.base)]:
            self.enterContext(patch.object(obj,name,value))
        self.enterContext(patch.object(forge.forge_learning,'recall',return_value=[]))
        self.task=forge.start(RAW,target=str(self.root),isolation='direct').task_id

    def git_run(self,*args):
        return subprocess.run([self.git,'-C',str(self.root),*args],capture_output=True,text=True,
            encoding='utf8',check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout.strip()

    def settings(self,ui,language):return patch.object(fl,'settings',return_value={'lang':ui,'agent':{'frame_language':language}})

    def test_real_git_checkpoints_preserve_authored_messages_bytes_and_revisions16axes(self):
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with self.subTest(ui=ui,frame=language),self.settings(ui,language):
                    raw=('start\r\n'+RAW+ui+language).encode('utf8');self.path.write_bytes(raw)
                    before=self.git_run('rev-parse','HEAD')
                    answer=forge.checkpoint(self.task,RAW)
                    after=self.git_run('rev-parse','HEAD');self.assertNotEqual(before,after)
                    short=self.git_run('rev-parse','--short','HEAD')
                    self.assertEqual(answer,fl.text('forge.execution.41',v1=short,v3=RAW))
                    self.assertEqual(self.git_run('log','-1','--format=%B'),RAW)
                    self.assertEqual(self.path.read_bytes(),raw)
                    self.assertEqual(forge.checkpoint(self.task,RAW),fl.text('forge.execution.38'))

    def test_post_commit_lock_error_reports_unconfirmed_without_claiming_no_effect(self):
        original_lock=forge._mutation_lock
        @contextmanager
        def late_error(*args,**kwargs):
            with original_lock(*args,**kwargs) as beat:yield beat
            raise TimeoutError(RAW)
        for language in fl.LANGUAGES:
            with self.settings('zh',language),patch.object(forge,'_mutation_lock',side_effect=late_error):
                raw=(RAW+language).encode('utf8');self.path.write_bytes(raw);before=self.git_run('rev-parse','HEAD')
                answer=forge.checkpoint(self.task,RAW)
                self.assertEqual(answer,fl.text('forge.execution.44',v1=RAW))
            self.assertNotEqual(self.git_run('rev-parse','HEAD'),before)
            self.assertEqual(self.path.read_bytes(),raw)
            self.assertEqual(self.git_run('log','-1','--format=%B'),RAW)

    def test_missing_argv_program_refuses_before_launch_with_exact_machine_facts16axes(self):
        program='missing-program-765b42'
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with self.subTest(ui=ui,frame=language),self.settings(ui,language), \
                     patch.object(command_execution,'launch',side_effect=AssertionError('refusal launched a child')) as launch:
                    slot=tool_report.new();token=tool_report.attach(slot)
                    try:answer=forge.run_command(self.task,[program])
                    finally:tool_report.detach(token)
                    self.assertEqual(slot['outcome'],'refused');launch.assert_not_called()
                    self.assertIn(fl.text('forge.execution.23',v1=repr(program)),answer)
                    line=next(line for line in answer.splitlines() if line.startswith('[run_command '))
                    facts=json.loads(line[len('[run_command '):-1])
                    self.assertEqual(facts['executor'],'forge task '+self.task)
                    self.assertEqual(facts['argv'],[program]);self.assertFalse(facts['started'])
                    self.assertEqual(facts['cwd'],str(self.root));self.assertNotIn('exit_code',facts)

    def test_localized_kill_producer_keeps_machine_termination_status16axes(self):
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                for code,status,key in [(0,'stopped','50'),(128,'already_exited','51'),(1,'unknown','52')]:
                    with self.subTest(ui=ui,frame=language,code=code),self.settings(ui,language), \
                         patch.object(forge,'os',SimpleNamespace(name='nt')),patch.object(forge,'_kill_identity',return_value=''), \
                         patch.object(forge.subprocess,'run',return_value=subprocess.CompletedProcess([],code,'',RAW)) as run:
                        result=forge._kill_tree(12345,'born')
                        kwargs={'v1':'1','v3':RAW} if code==1 else {}
                        self.assertEqual(str(result),fl.text('forge.execution.'+key,**kwargs))
                        self.assertEqual(result.termination,status)
                        self.assertEqual(run.call_args.args[0],['taskkill','/PID','12345','/T','/F'])

    def test_checkpoint_pins_language_across_a_real_commit_then_uses_saved_change(self):
        settings={'lang':'zh','agent':{'frame_language':'en'}};original_run=forge._run
        def execute(*args,**kwargs):
            value=original_run(*args,**kwargs)
            if 'commit' in args[0]:settings['agent']['frame_language']='fr'
            return value
        self.path.write_bytes(RAW.encode('utf8'))
        with patch.object(fl,'settings',side_effect=lambda:settings),patch.object(forge,'_run',side_effect=execute):
            answer=forge.checkpoint(self.task,RAW)
            short=self.git_run('rev-parse','--short','HEAD')
            with fl.use('en'):self.assertEqual(answer,fl.text('forge.execution.41',v1=short,v3=RAW))
            self.assertEqual(forge.checkpoint(self.task,RAW),fl.text('forge.execution.38'))
