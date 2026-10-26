"""Startup must not probe WSL; automatic Windows launches stay reachable in tray."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK))
import python_app
import python_tray
from localharness import fence, runner


class Event:
    def __init__(self): self.callbacks = []
    def __iadd__(self, callback): self.callbacks.append(callback); return self
    def fire(self):
        for callback in self.callbacks: callback()


def window():
    value = Mock()
    value.events = SimpleNamespace(**{name: Event() for name in ('shown', 'maximized', 'restored', 'loaded')})
    return value


class StartupShell(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows startup shell resolution')
    def test_loading_agent_does_not_lookup_or_execute_a_shell(self):
        modules = {n: ModuleType(n) for n in ('agent', 'memory_life', 'owner_context', 'sitecustomize')}
        modules['owner_context'].configure = Mock()
        before_path = sys.path[:]
        try:
            with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, modules), patch.dict(os.environ):
                with patch.object(runner.boot, 'env_knobs', return_value={}), patch.object(runner.boot, 'dotenv_gate'), patch.object(runner, '_publish_fold_policy'):
                    with patch('shutil.which', return_value='C:/Windows/System32/bash.exe') as lookup, patch('subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='praxis-shell-ok')) as execute:
                        result = runner._load_tree(Path(tmp), Path(tmp), {})
                self.assertEqual(result, (modules['agent'], modules['memory_life']))
                lookup.assert_not_called()
                execute.assert_not_called()
        finally:
            sys.path[:] = before_path
            runner._agent = runner._life = None

    @unittest.skipUnless(os.name == 'nt', 'Windows packaged shell binding')
    def test_unfenced_shell_uses_own_absolute_executable_even_with_hostile_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); own = root/'runtime/bash.exe'
            own.parent.mkdir(); own.write_bytes(b'fixture-only')
            scope = SimpleNamespace(run=Mock(return_value=subprocess.CompletedProcess([], 0, stdout=b'ok')))
            with patch.dict(sys.modules, process_scope=scope), patch.dict(os.environ, PATH='C:/Windows/System32'):
                shim = fence._SubprocessShim(subprocess, None, root/'workspace', root)
                self.assertEqual(shim.run(['bash', '-lc', 'echo ok']).stdout, 'ok')
            self.assertEqual(scope.run.call_args.args[0], [str(own.resolve()), '-lc', 'echo ok'])

    @unittest.skipUnless(os.name == 'nt', 'Windows packaged shell binding')
    def test_missing_own_shell_is_not_replaced_with_system_bash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); own = root/'runtime/bash.exe'
            def missing(argv, **kwargs):
                self.assertEqual(argv[0], str(own.resolve()))
                raise FileNotFoundError(str(own))
            scope = SimpleNamespace(run=Mock(side_effect=missing))
            with patch.dict(sys.modules, process_scope=scope):
                with self.assertRaises(FileNotFoundError):
                    fence._SubprocessShim(subprocess, None, root, root).run(['bash', '-lc', 'echo ok'])
            self.assertEqual(scope.run.call_count, 1)

    @unittest.skipUnless(os.name == 'nt', 'Windows packaged shell binding')
    def test_workshop_and_container_failure_keep_own_shell_and_caller_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cwd = root/'project'
            own = str((root/'runtime/bash.exe').resolve())
            for container in (None, SimpleNamespace(run=Mock(side_effect=OSError('fixture container unavailable')))):
                scope = SimpleNamespace(run=Mock(return_value=subprocess.CompletedProcess([], 0, stdout=b'ok')))
                with patch.dict(sys.modules, process_scope=scope), patch.dict(fence.STATE):
                    shim = fence._SubprocessShim(subprocess, container, root/'workspace', root, route_all=True)
                    self.assertEqual(shim.run('echo ok', shell=True, cwd=cwd).stdout, 'ok')
                self.assertEqual(scope.run.call_args.args[0], [own, '-lc', 'echo ok'])
                self.assertNotIn('shell', scope.run.call_args.kwargs)
                self.assertEqual(scope.run.call_args.kwargs['cwd'], cwd)


class AutomaticWindow(unittest.TestCase):
    def test_automatic_windows_hidden_manual_and_other_platforms_visible(self):
        screen = SimpleNamespace(x=0, y=0, width=1920, height=1080,
                                 frame=SimpleNamespace(X=0, Y=0, Width=1920, Height=1040))
        cases = [('win32', 'automatic', True, False, False), ('win32', 'manual', False, False, False),
                 ('linux', 'automatic', False, False, False), ('darwin', 'automatic', False, False, False),
                 ('win32', 'automatic', True, True, False), ('win32', 'automatic', True, True, True)]
        for platform, intent, hidden, relaunch, inline in cases:
            with self.subTest(platform=platform, intent=intent), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); (root/'app/static').mkdir(parents=True)
                (root/'app/static/index.html').write_text('<html>fixture</html>')
                (root/'helene.json').write_text('{"setup_complete":true}')
                class Host:
                    def __init__(self, *args): pass
                    def start(self): pass
                    def readiness(self, **kwargs): return {}
                    def channel(self): return {}
                    def close(self): pass
                webview = SimpleNamespace(settings={}, screens=[screen], create_window=Mock(return_value=window()), start=Mock())
                application = python_app.Application
                def create(*args):
                    app = application(*args); app.relaunch = relaunch; return app
                launch = ['--root', str(root)] + ([f'--launch-intent={intent}'] if inline else ['--launch-intent', intent])
                with patch.dict(sys.modules, webview=webview), patch.object(python_app.sys, 'platform', platform), patch.object(python_app, 'Controller', Host):
                    with patch('localharness.owner_stop.stopped', return_value=False), patch('localharness.portable_start.initialize'), patch.object(python_app, 'Application', side_effect=create), patch.object(python_app.os, 'execv') as execute:
                        self.assertEqual(python_app._main(launch), 0)
                if relaunch:
                    self.assertEqual(execute.call_args.args[1][-2:], ['--launch-intent', 'manual'])
                    self.assertNotIn('automatic', execute.call_args.args[1])
                    self.assertNotIn('--launch-intent=automatic', execute.call_args.args[1])
                    self.assertIn(str(root), execute.call_args.args[1])
                else: execute.assert_not_called()
                self.assertEqual(webview.create_window.call_args.kwargs.get('hidden', False), hidden)

    def test_tray_failure_shows_hidden_window_and_success_does_not(self):
        system = ModuleType('System'); system.Action = lambda fn: fn
        forms = ModuleType('System.Windows.Forms'); forms.Screen = SimpleNamespace(FromHandle=lambda handle: SimpleNamespace(WorkingArea='fixture'))
        for success in (False, True):
            with self.subTest(tray_success=success), tempfile.TemporaryDirectory() as tmp:
                app = python_app.Application(Path(tmp), Path(tmp)); app.window = window(); app.start_in_tray = True
                app.window.native.Invoke.side_effect = lambda fn: fn()
                def install(app):
                    if success: app.tray = object()
                    else: raise RuntimeError('fixture tray initialization failure')
                try:
                    with patch.dict(sys.modules, {'System':system, 'System.Windows.Forms':forms}), patch.object(python_app.sys, 'platform', 'win32'), patch.object(python_app, 'set_window_icon'), patch.object(python_tray, 'install', side_effect=install):
                        python_app.bind_window_state(app)
                        app.window.events.shown.fire()
                    self.assertTrue(app.control_file.is_file())
                    self.assertEqual(app.window.show.call_count, 0 if success else 1)
                finally:
                    app.server.server_close()
                    app.control_file.unlink(missing_ok=True)


if __name__ == '__main__': unittest.main()
