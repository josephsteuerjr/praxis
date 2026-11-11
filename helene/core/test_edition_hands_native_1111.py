"""Actual local children: admission, timeout, cancellation and background lifetime."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import json
import unittest
from unittest.mock import patch

import command_execution as commands
import agent
import hand_transport
import hands
import process_scope
import python_execution


class NativeHands(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = 'native-hands-' + self.root.name

    def execute(self, code, timeout=5):
        with process_scope.bind(self.scope), python_execution.source_input(code) as source:
            return hands.execute_argv(python_execution.argv(), str(self.root), timeout,
                                      stdin=source, base=self.root)

    def test_python_helpers_keep_mounted_read_write_and_home_code_boundaries(self):
        home=self.root/'home';home.mkdir();(home/'workspace').mkdir()
        external=self.root/'mounted';external.mkdir()
        file=external/'note.txt';file.write_text('old\r\n',encoding='utf8',newline='')
        policy={'open':False,'homes':[str(home)],'read':[str(external)],'write':[]}
        code=f'''text,sha=h.read({str(file)!r})
assert text=='old\\r\\n',repr(text)
try:h.edit({str(file)!r},'old','new',expected_sha256=sha)
except h.Refused:print('READ-ONLY')
else:raise AssertionError('read-only mount was writable')
'''
        with patch.dict(os.environ,{'PRAXIS_BASE':str(home)}),patch.object(commands,'home_file_policy',lambda:policy):
            result=self.execute(code);self.assertEqual(result['code'],0,result['out']);self.assertIn('READ-ONLY',result['out'])
            policy['write']=[str(external)]
            result=self.execute(f"text,sha=h.read({str(file)!r});h.edit({str(file)!r},'old','new',expected_sha256=sha)")
            self.assertEqual(result['code'],0,result['out'])
            policy['open']=True
            target=home/'agent.py';target.write_text('code',encoding='utf8')
            result=self.execute(f'''try:h.write({str(target)!r},'changed',overwrite=True,force=True)
except h.Refused:print('CODE-PROTECTED')
else:raise AssertionError('open mode bypassed code proposal policy')
''')
            self.assertEqual(result['code'],0,result['out']);self.assertIn('CODE-PROTECTED',result['out'])
        self.assertEqual(file.read_bytes(),b'new\r\n');self.assertEqual(target.read_text(),'code')

    def test_live_identity_and_real_child_ancestry(self):
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'],
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            self.assertTrue(hand_transport.descendant(proc.pid, os.getpid()))
            self.assertFalse(hand_transport.descendant(os.getpid(), proc.pid))
            with hand_transport.process_pin(proc.pid) as (_, born, live):
                self.assertGreater(born, 0)
                proc.terminate();proc.wait(5)
                self.assertFalse(live())
        finally:
            if proc.poll() is None:proc.terminate();proc.wait(5)
        self.assertFalse(hand_transport.descendant(proc.pid, os.getpid()))

    def test_timeout_stops_real_grandchild(self):
        marker = self.root/'escaped'
        child = "import time;from pathlib import Path;time.sleep(2);Path('escaped').write_text('bad')"
        result = self.execute(f'import subprocess,sys,time\nsubprocess.Popen([sys.executable,"-c",{child!r}])\nprint("started",flush=True)\ntime.sleep(20)', 1)
        self.assertTrue(result['killed'],result)
        time.sleep(1.3)
        self.assertFalse(marker.exists())

    def test_explicit_background_can_complete_after_normal_exit(self):
        child = "import time;from pathlib import Path;time.sleep(.3);Path('background').write_text('ok')"
        result = self.execute(f'import subprocess,sys\np=subprocess.Popen([sys.executable,"-c",{child!r}])\nprint(p.pid)')
        import psutil
        try:
            child_process = psutil.Process(int(result['out'].strip()))
            child_process.wait(5)
        except psutil.NoSuchProcess:
            pass
        self.assertEqual(result['code'],0,result)
        end=time.monotonic()+5
        while not (self.root/'background').exists() and time.monotonic()<end:time.sleep(.05)
        self.assertEqual((self.root/'background').read_text(),'ok')

    def test_closed_scope_never_executes_child(self):
        process_scope.cancel(self.scope)
        with self.assertRaisesRegex(RuntimeError,'admission closed'):
            self.execute("from pathlib import Path;Path('forbidden').write_text('bad')")
        self.assertFalse((self.root/'forbidden').exists())

    def test_cancel_interrupts_running_child_and_grandchild(self):
        def cancel():
            end=time.monotonic()+5
            while not (self.root/'started').exists() and time.monotonic()<end:time.sleep(.01)
            process_scope.cancel(self.scope)
        thread=threading.Thread(target=cancel);thread.start()
        start=time.monotonic()
        try:
            result=self.execute("from pathlib import Path;import time;Path('started').write_text('yes');time.sleep(20)",10)
        finally:thread.join(6)
        self.assertNotEqual(result['code'],0,result)
        self.assertLess(time.monotonic()-start,7)

    def test_fence_adapter_gets_exact_stdin_and_no_plain_retry(self):
        def fenced(argv,cwd,timeout,*,stdin,env):
            self.assertEqual(stdin.read(),b'print(42)')
            self.assertEqual(cwd,self.root)
            raise OSError('container unavailable')
        with patch.object(commands,'home_runner',fenced), patch.object(commands,'launch') as launch:
            result=self.execute('print(42)')
        self.assertFalse(result['ok']);launch.assert_not_called()

    def test_output_decoder_preserves_mixed_windows_bytes(self):
        import locale
        with patch.object(locale,'getpreferredencoding',return_value='cp1251'), \
             patch.object(commands,'output_decoder',agent._decode_shell_output):
            result=self.execute("import sys;sys.stdout.buffer.write('привет'.encode('cp1251')+b'\\n'+'мир'.encode('utf8'))")
        self.assertEqual(result['out'],'привет\nмир')

if __name__=='__main__':unittest.main()
