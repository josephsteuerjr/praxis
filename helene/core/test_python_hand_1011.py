"""Real direct-Python processes, task custody and the sovereign shell boundary."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import agent
import forge
import tool_report


class PythonHand(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(agent,'BASE',self.root))
        self.stack.enter_context(patch.object(agent,'_autocommit_self_edit'))
        self.stack.enter_context(patch.object(agent,'_pre_shell_dirt',return_value={}))
        self.stack.enter_context(patch.object(agent.selfgit,'safety_point'))

    def call(self,code,**kw):
        report=tool_report.new();token=tool_report.attach(report)
        try:return agent.tool_python(code,cwd=str(self.root),**kw),report
        finally:tool_report.detach(token)

    def test_python_source_is_not_shell_syntax_and_argv_is_python(self):
        value="quotes ' and \"; $HOME `touch DO-NOT-CREATE`\nPY\nкириллица"
        code='import json\nvalue='+repr(value)+'\nprint(json.dumps(value,ensure_ascii=False))\n'
        with patch.object(agent,'_tool_trace_event') as trace:
            out,report=self.call(code)
        self.assertEqual(json.loads(out.splitlines()[0]),value)
        self.assertTrue(out.endswith('[exit 0]'),out)
        self.assertEqual(report['outcome'],'ok')
        self.assertFalse((self.root/'DO-NOT-CREATE').exists())
        execution=next(c.kwargs['receipt'] for c in trace.call_args_list if c.args[0]=='tool_execution')
        self.assertEqual(execution['argv'][0],sys.executable)
        self.assertNotIn(code,execution['argv'])

    def test_h_is_loaded_from_core_even_with_a_same_named_project_module(self):
        (self.root/'praxis_hands.py').write_text('raise RuntimeError("wrong library")')
        out,_=self.call('print(callable(h.read), callable(h.edit))')
        self.assertIn('True True',out)

    def test_traceback_lines_are_the_original_source_lines(self):
        out,report=self.call('x=1\nx+=1\nraise RuntimeError("source-line")')
        self.assertIn('File "<python>", line 3',out)
        self.assertIn('source-line',out)
        self.assertEqual(report['outcome'],'failed')

    def test_full_output_is_retained_for_the_normal_result_spine(self):
        out,_=self.call('print("x" * 50000)')
        self.assertEqual(out.splitlines()[0],'x'*50000)

    def test_home_h_reads_and_edits_with_a_version_without_import(self):
        (self.root/'workspace').mkdir()
        p=self.root/'workspace/a.txt';p.write_text('one\n')
        with patch.dict(os.environ,{'PRAXIS_BASE':str(self.root)}):
            out,report=self.call('text,v=h.read("workspace/a.txt")\nh.edit("workspace/a.txt","one","two",expected_sha256=v)\nprint("H-OK")')
        self.assertIn('H-OK',out)
        self.assertEqual(p.read_text(),'two\n')
        self.assertEqual(report['outcome'],'ok')

    def test_python_inherits_forge_task_custody_and_root(self):
        (self.root/'a.txt').write_text('one\n')
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        subprocess.run(['git','-C',str(self.root),'add','.'],check=True)
        subprocess.run(['git','-C',str(self.root),'-c','user.name=T','-c','user.email=t@local','commit','-qm','fixture'],check=True)
        task=re.search(r'code-[0-9a-f]+',forge.start('python fixture',target=str(self.root),isolation='direct')).group()
        code='hits=h.search("one",glob="**/*.txt")\nassert "a.txt" in hits, hits\ntext,v=h.read("a.txt")\nh.edit("a.txt","one","two",expected_sha256=v)\nprint("TASK-OK")'
        out=agent.tool_python(code,task_id=task,timeout=20)
        self.assertIn('TASK-OK',out)
        self.assertEqual((self.root/'a.txt').read_text(),'two\n')
        records=list((forge._task_dir(task)/'processes').glob('*/request.json'))
        self.assertEqual(len(records),1)
        record=json.loads(records[0].read_text())
        self.assertTrue(record['script_edit_context'])
        self.assertEqual(json.loads(records[0].with_name('result.json').read_text())['status'],'done')

    def test_remote_task_is_refused_before_dispatch(self):
        remote=object()
        with patch.object(forge,'_task_root',return_value=({'scope':'host'},Path('/host'),'')), \
             patch.object(forge,'_remote_client',return_value=remote):
            out=agent.tool_python('raise AssertionError("must not execute")',task_id='hcode-fixture')
        self.assertIn('refused before execution',out)

    def test_shell_can_still_use_an_absolute_directory_outside_home(self):
        with tempfile.TemporaryDirectory() as directory:
            # pwd may render an MSYS path. Prove the actual directory by a file.
            Path(directory,'cwd-witness').write_text('selected-directory')
            out=agent.tool_shell('cat cwd-witness',cwd=directory)
            self.assertIn('selected-directory',out)
            self.assertTrue(out.endswith('[exit 0]'),out)

    def test_shell_timeout_stops_ordinary_descendants(self):
        marker=self.root/'late'
        child='import time,pathlib;time.sleep(3);pathlib.Path('+repr(marker.as_posix())+').write_text("late")'
        parent='import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",'+repr(child)+']);time.sleep(8)'
        out=agent.tool_shell(shlex.quote(sys.executable)+' -c '+shlex.quote(parent),cwd=str(self.root),timeout=1)
        self.assertIn('таймауту 1s',out)
        self.assertNotIn('SyntaxError',out)
        time.sleep(3)
        self.assertFalse(marker.exists())

    def test_shell_background_command_still_outlives_its_shell(self):
        marker=self.root/'background'
        started=time.monotonic()
        out=agent.tool_shell('(sleep 2; printf ok > '+shlex.quote(str(marker))+') &',cwd=str(self.root),timeout=5)
        self.assertLess(time.monotonic()-started,1.6)
        self.assertFalse(marker.exists())
        deadline=time.monotonic()+3
        while not marker.exists() and time.monotonic()<deadline:time.sleep(.05)
        self.assertEqual(marker.read_text(),'ok')
        self.assertIn('[exit 0]',out)


if __name__=='__main__':unittest.main()
