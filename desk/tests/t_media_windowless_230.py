"""All production ffprobe/ffmpeg calls carry the Windows hidden-console policy."""
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / 'localharness')]
from localharness import telegram_media


class MediaWindowless(unittest.TestCase):
    def test_header_duration_and_frame_processes_are_all_hidden(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            home = tree / 'memory/telegram-media'
            home.mkdir(parents=True)
            source = home / 'test.gif'
            source.write_bytes(b'local fixture, decoder itself is substituted')
            def execute(argv, **kw):
                if '-nofind_stream_info' in argv:
                    return SimpleNamespace(stdout=b'{"streams":[{"width":32,"height":24}]}')
                if 'format=duration' in argv:
                    return SimpleNamespace(stdout=b'{"format":{"duration":"1"}}')
                Path(argv[-1]).write_bytes(b'frame fixture; not a PNG qualification')
                return SimpleNamespace(returncode=0)
            with (patch.object(telegram_media.shutil, 'which', side_effect=lambda name: name),
                  patch.object(subprocess, 'run', side_effect=execute) as run):
                frames = telegram_media.vision_frames(tree, source, 1)
            self.assertEqual(len(frames), 1)
            self.assertEqual(run.call_count, 3)
            for call in run.call_args_list:
                self.assertEqual(call.kwargs['creationflags'], getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.assertEqual(call.kwargs['timeout'], 3)
                self.assertTrue(call.kwargs['check'])
                self.assertNotIn('shell', call.kwargs)


class OtherHelperWindowless(unittest.TestCase):
    def test_cloudflared_version_probe_is_hidden_and_still_exact(self):
        from localharness.phone_tunnel import cloudflare_tunnel as tunnel
        answer = SimpleNamespace(returncode=0, stderr=b'',
            stdout=('cloudflared version ' + tunnel.CLOUDFLARED_VERSION + '\n').encode())
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch.object(tunnel.subprocess, 'run', return_value=answer) as run:
                tunnel._verify_version(home / 'cloudflared.exe', home)
            self.assertEqual(run.call_args.kwargs['creationflags'], getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(run.call_args.kwargs['timeout'], 3)
            self.assertEqual(run.call_args.args[0][-1], '--version')
            answer.stdout = b'cloudflared version wrong-version\n'
            with patch.object(tunnel.subprocess, 'run', return_value=answer):
                with self.assertRaises(tunnel.CloudflaredError):
                    tunnel._verify_version(home / 'cloudflared.exe', home)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows gate CLI contract')
    def test_windows_gate_all_commands_use_hidden_console_policy(self):
        import windows_safe_gate as gate
        import types
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            edition, package, relay, logs = (root / part for part in ('edition', 'package', 'relay', 'logs'))
            for path in (edition / 'body/target/debug/praxis-body.exe',
                         edition / 'body/target/debug/praxis-bridge.exe',
                         package / 'localharness/cu_read_cli.py', package / 'localharness/cu_fs_cli.py'):
                path.parent.mkdir(parents=True, exist_ok=True); path.touch()
            def execute(argv, **kw):
                kw['stdout'].write(b'# pass 1\n')
                return SimpleNamespace(returncode=0)
            argv = ['gate', '--edition', str(edition), '--package', str(package),
                    '--relay', str(relay), '--logs', str(logs)]
            with (patch.object(sys, 'argv', argv), patch.object(gate, 'PYTHON_TESTS', ('fixture',)),
                  patch.object(gate.shutil, 'which', side_effect=lambda name: name),
                  patch.dict(sys.modules, {'test_receipt': types.SimpleNamespace(log_count=lambda *a: 1)}),
                  patch.object(gate.subprocess, 'run', side_effect=execute) as run,
                  patch('builtins.print')):
                self.assertEqual(gate.main(), 0)
            self.assertEqual(run.call_count, 7)
            for call in run.call_args_list:
                self.assertEqual(call.kwargs['creationflags'], getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.assertEqual(call.kwargs['timeout'], 3600)
                self.assertNotIn('shell', call.kwargs)


if __name__ == '__main__':
    unittest.main()
