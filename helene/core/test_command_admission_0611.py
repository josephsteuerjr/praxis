"""Native deferred command admission, exact IO and parent-death contracts."""
import contextlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import command_process
from command_process import CommandProcess


class CommandAdmission(unittest.TestCase):
    def test_default_immediate_call_keeps_exit_code(self):
        process=CommandProcess([sys.executable,'-B','-c','import sys;sys.exit(7)'],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:self.assertEqual(process.wait(5),7)
        finally:process.stop_tree()

    def exercise_io(self, *, pipe_gate=False, shell=False, relative_custody=False):
        with tempfile.TemporaryDirectory() as temporary,contextlib.ExitStack() as stack:
            root=Path(temporary);marker=root/'effect'
            working=root/'nested/target' if relative_custody else root
            working.mkdir(parents=True,exist_ok=True)
            payload=b'exact stdin\x00\xff\n'
            (root/'input').write_bytes(payload)
            source=stack.enter_context((root/'input').open('rb'))
            output=stack.enter_context((root/'output').open('wb'))
            error=stack.enter_context((root/'error').open('wb'))
            code=('import os,sys;from pathlib import Path;'
                  f'assert Path.cwd()==Path({str(working)!r});'
                  'sys.stdout.buffer.write(sys.stdin.buffer.read());'
                  'sys.stderr.write(os.environ["HELENE_ADMISSION_FIXTURE"]);'
                  f'Path({str(marker)!r}).write_text("executed")')
            args=[sys.executable,'-B','-c',code]
            command=(subprocess.list2cmdline(args) if os.name=='nt' else shlex.join(args)) if shell else args
            if pipe_gate:stack.enter_context(mock.patch.object(command_process.sys,'platform','darwin'))
            custody=os.path.relpath(root/'custody',Path.cwd()) if relative_custody else root/'custody'
            process=CommandProcess(command,shell=shell,defer_start=True,custody_directory=custody,
                cwd=working,env={**os.environ,'HELENE_ADMISSION_FIXTURE':'exact stderr'},
                stdin=source,stdout=output,stderr=error)
            try:
                time.sleep(.05)
                self.assertFalse(marker.exists())
                self.assertIsNone(process.poll())
                self.assertEqual(process.custody_identity()['pid'],process.pid)
                process.admit();self.assertEqual(process.wait(5),0)
                self.assertTrue(marker.exists())
                output.flush();error.flush()
                self.assertEqual((root/'output').read_bytes(),payload)
                self.assertEqual((root/'error').read_bytes(),b'exact stderr')
            finally:process.stop_tree()

    def test_list_arguments_preserve_stdio_environment_and_cwd(self):
        self.exercise_io()

    def test_shell_string_preserves_stdio_environment_and_cwd(self):
        self.exercise_io(shell=True)

    @unittest.skipUnless(os.name=='posix','POSIX admission descriptor')
    def test_posix_pipe_gate_preserves_exact_io(self):
        self.exercise_io(pipe_gate=True)

    @unittest.skipUnless(os.name=='posix','POSIX helper path resolution')
    def test_relative_custody_directory_survives_target_cwd(self):
        self.exercise_io(relative_custody=True)

    @unittest.skipUnless(os.name=='posix','POSIX pre-exec admission boundary')
    def test_deferred_preexec_is_refused_before_any_child_or_effect(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);marker=root/'forbidden';process=None
            try:
                with mock.patch.object(command_process.subprocess,'Popen',wraps=subprocess.Popen) as launch:
                    with self.assertRaisesRegex(ValueError,'preexec_fn'):
                        process=CommandProcess([sys.executable,'-B','-c','pass'],defer_start=True,
                            custody_directory=root/'custody',preexec_fn=lambda:marker.touch(),
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    launch.assert_not_called()
                self.assertFalse(marker.exists())
            finally:
                if process is not None:process.stop_tree()

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux deferred descriptor constraints')
    def test_linux_unsupported_descriptor_modes_refuse_before_child(self):
        read_fd,write_fd=os.pipe()
        try:
            for options in ({'pass_fds':(read_fd,)},{'close_fds':False}):
                with self.subTest(options=options),mock.patch.object(command_process.subprocess,'Popen') as launch:
                    with self.assertRaisesRegex(ValueError,'close_fds=True and empty pass_fds'):
                        CommandProcess([sys.executable,'-B','-c','pass'],defer_start=True,**options)
                    launch.assert_not_called()
        finally:os.close(read_fd);os.close(write_fd)

    @unittest.skipUnless(os.name=='posix','POSIX isolated admission helpers')
    def test_target_sitecustomize_waits_for_admission_and_retains_environment(self):
        for pipe_gate in (False,True):
            with self.subTest(pipe_gate=pipe_gate),tempfile.TemporaryDirectory() as temporary,contextlib.ExitStack() as stack:
                root=Path(temporary);marker=root/'site-effect'
                (root/'sitecustomize.py').write_text(
                    'from pathlib import Path;Path('+repr(str(marker))+').touch()',encoding='utf-8')
                output=stack.enter_context((root/'output').open('wb'))
                if pipe_gate:stack.enter_context(mock.patch.object(command_process.sys,'platform','darwin'))
                process=CommandProcess([sys.executable,'-B','-c','import os;print(os.environ["PYTHONPATH"])'],
                    env={**os.environ,'PYTHONPATH':str(root)},defer_start=True,
                    custody_directory=root/'custody',stdout=output,stderr=subprocess.DEVNULL)
                try:
                    time.sleep(.05);self.assertFalse(marker.exists())
                    process.admit();self.assertEqual(process.wait(5),0)
                    self.assertTrue(marker.exists())
                    output.flush()
                    self.assertEqual((root/'output').read_text(encoding='utf-8').strip(),str(root))
                finally:process.stop_tree()

    @unittest.skipUnless(os.name=='posix','POSIX inherited descriptors')
    def test_posix_pipe_gate_preserves_close_fds_false(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);read_fd,write_fd=os.pipe();os.set_inheritable(read_fd,True)
            try:
                os.write(write_fd,b'inherited descriptor')
                with mock.patch.object(command_process.sys,'platform','darwin'),(root/'output').open('wb') as output:
                    process=CommandProcess([sys.executable,'-B','-c',
                        f'import os,sys;sys.stdout.buffer.write(os.read({read_fd},64))'],
                        close_fds=False,defer_start=True,custody_directory=root/'custody',
                        stdout=output,stderr=subprocess.DEVNULL)
                    try:process.admit();self.assertEqual(process.wait(5),0)
                    finally:process.stop_tree()
                self.assertEqual((root/'output').read_bytes(),b'inherited descriptor')
            finally:os.close(read_fd);os.close(write_fd)

    @unittest.skipUnless(os.name=='nt','Windows suspended Job Object')
    def test_windows_parent_death_before_admission_never_executes(self):
        import psutil
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);marker=root/'forbidden';identity=root/'identity.json'
            effect='from pathlib import Path;Path('+repr(str(marker))+').touch()'
            script=('import os,sys,json;from pathlib import Path;'
                    f'sys.path.insert(0,{str(Path(command_process.__file__).parent)!r});'
                    'import command_process,psutil;'
                    f'p=command_process.CommandProcess([sys.executable,"-B","-c",{effect!r}],'
                    'defer_start=True,stdout=-3,stderr=-3);'
                    f'Path({str(identity)!r}).write_text(json.dumps({{"pid":p.pid,"birth":psutil.Process(p.pid).create_time()}}));'
                    'os._exit(91)')
            parent=subprocess.run([sys.executable,'-B','-c',script],timeout=8,
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            self.assertEqual(parent.returncode,91,parent.stdout)
            row=json.loads(identity.read_text(encoding='utf-8'))
            def bound_child():
                try:
                    child=psutil.Process(row['pid'])
                    return child if child.create_time()==row['birth'] else None
                except psutil.NoSuchProcess:return None
            try:
                end=time.monotonic()+5
                while bound_child() is not None and time.monotonic()<end:time.sleep(.02)
                self.assertIsNone(bound_child())
                self.assertFalse(marker.exists())
            finally:
                child=bound_child()
                if child is not None:child.kill();child.wait(5)

    @unittest.skipUnless(os.name=='posix','POSIX parent-death admission pipe')
    def test_parent_death_before_admission_never_executes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);marker=root/'forbidden';custody=root/'custody'
            script=('import os,sys;from pathlib import Path;'
                    f'sys.path.insert(0,{str(Path(command_process.__file__).parent)!r});'
                    'import command_process;command_process.sys.platform="darwin";'
                    f'command_process.CommandProcess([sys.executable,"-B","-c",'
                    f'{("from pathlib import Path;Path("+repr(str(marker))+").touch()")!r}],'
                    f'defer_start=True,custody_directory=Path({str(custody)!r}),'
                    'stdout=-3,stderr=-3);os._exit(91)')
            parent=subprocess.run([sys.executable,'-B','-c',script],timeout=8,
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            self.assertEqual(parent.returncode,91,parent.stdout)
            end=time.monotonic()+5
            while not (custody/'settled.json').exists() and time.monotonic()<end:time.sleep(.02)
            receipt=json.loads((custody/'settled.json').read_text(encoding='utf-8'))
            self.assertTrue(receipt['stopped']);self.assertTrue(receipt['settled'])
            self.assertFalse(marker.exists())


if __name__=='__main__':unittest.main()
