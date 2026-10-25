"""Offline native adapter contract, not a native keyboard end-to-end test."""
import ast
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

SOURCE = Path(os.environ.get('RELOAD_SOURCE', Path(__file__).resolve().parents[1] / 'python_app.py'))


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self, *args):
        for handler in self.handlers:
            handler(*args)


class NativeReload(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'bind_native_reload')
        scope = {'sys': types.SimpleNamespace(platform='win32')}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SOURCE), 'exec'), scope)
        self.bind = scope['bind_native_reload']
        self.control = types.SimpleNamespace(ModifierKeys=0)
        self.keys = types.SimpleNamespace(Control=2, **{'None': 0})
        self.modules = {'System.Reflection': types.SimpleNamespace(BindingFlags=types.SimpleNamespace(Instance=1, NonPublic=2)),
                        'System': types.SimpleNamespace(Action=lambda fn: fn),
                        'System.Windows.Forms': types.SimpleNamespace(Control=self.control, Keys=self.keys)}
        self.scripts, self.queue = [], []
        self.accel, self.key, self.loaded = Event(), Event(), Event()
        self.view = types.SimpleNamespace(CoreWebView2=types.SimpleNamespace(ExecuteScriptAsync=self.scripts.append),
            CoreWebView2Controller=types.SimpleNamespace(AcceleratorKeyPressed=self.accel), ContainsFocus=True)
        def get_field(name, flags):
            self.assertEqual(name, '_coreWebView2Controller')
            self.assertEqual(flags, 3)
            return types.SimpleNamespace(GetValue=lambda view: self.view.CoreWebView2Controller)
        self.view.GetType = lambda: types.SimpleNamespace(GetField=get_field)
        self.form = types.SimpleNamespace(browser=types.SimpleNamespace(webview=self.view),
            Invoke=lambda fn: fn(), BeginInvoke=self.queue.append, KeyDown=self.key)
        self.app = types.SimpleNamespace(window=types.SimpleNamespace(native=self.form,
            events=types.SimpleNamespace(loaded=self.loaded)))
        self.bind(self.app)
        with patch.dict(sys.modules, self.modules):
            self.loaded.fire()
            self.loaded.fire()  # navigation must not duplicate handlers
        self.assertEqual(len(self.accel.handlers), 1)

    def accelerator(self, key=116, modifiers=0, repeat=False, kind='KeyDown'):
        self.control.ModifierKeys = modifiers
        e = types.SimpleNamespace(KeyEventKind=kind, VirtualKey=key, Handled=False,
                                  PhysicalKeyStatus=types.SimpleNamespace(WasKeyDown=repeat))
        self.accel.fire(None, e)
        return e

    def test_native_accelerator_defers_to_safe_bridge(self):
        self.assertTrue(self.accelerator().Handled)
        self.assertEqual(self.scripts, [])  # never execute JS inside native callback
        self.queue.pop()()
        self.assertEqual(self.scripts, ['window.__HELENE__?.requestReload?.();'])
        self.assertTrue(self.accelerator(82, 2).Handled)

    def test_modifiers_repeats_keyup(self):
        for key, modifiers in [(116, 2), (116, 1), (82, 0), (82, 3), (123, 0)]:
            self.assertFalse(self.accelerator(key, modifiers).Handled)
        self.assertFalse(self.accelerator(kind='KeyUp').Handled)
        self.assertTrue(self.accelerator(repeat=True).Handled)
        self.assertEqual(self.queue, [])

    def test_host_focus_fallback(self):
        e = types.SimpleNamespace(KeyCode=116, Modifiers=0, Handled=False)
        self.key.fire(None, e)
        self.assertFalse(e.Handled)
        self.view.ContainsFocus = False
        self.key.fire(None, e)
        self.assertTrue(e.Handled)
        self.assertTrue(e.SuppressKeyPress)
        self.assertEqual(len(self.queue), 1)

    @unittest.skipUnless(sys.platform == 'win32', 'Bundled Windows SDK contract')
    def test_bundled_sdk_contract(self):
        # Import the real bundled assemblies, but create no window and steal no focus.
        import clr
        import webview.platforms.edgechromium
        from Microsoft.Web.WebView2.WinForms import WebView2
        from Microsoft.Web.WebView2.Core import CoreWebView2Controller, CoreWebView2AcceleratorKeyPressedEventArgs
        from System.Reflection import BindingFlags
        typ = clr.GetClrType(WebView2)
        field = typ.GetField('_coreWebView2Controller', BindingFlags.Instance | BindingFlags.NonPublic)
        self.assertIsNotNone(field)
        self.assertEqual(field.FieldType, clr.GetClrType(CoreWebView2Controller))
        self.assertIsNotNone(clr.GetClrType(CoreWebView2Controller).GetEvent('AcceleratorKeyPressed'))
        args = clr.GetClrType(CoreWebView2AcceleratorKeyPressedEventArgs)
        for name in ['VirtualKey', 'KeyEventKind', 'PhysicalKeyStatus', 'Handled']:
            self.assertIsNotNone(args.GetProperty(name), name)

    def test_http_navigation_reinjects_safe_bridge(self):
        # Real Application HTTP handler with empty channel and isolated preferences.
        import tempfile
        from urllib.request import urlopen
        sys.path.insert(0, str(SOURCE.resolve().parent))
        from python_app import Application
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Application(root, root)
            app.serve()
            try:
                for _ in range(2):  # same HTTP path used by initial load and reload
                    with urlopen(app.origin) as response:
                        html = response.read().decode('utf-8')
                    self.assertIn("reloadCapability='safe-renderer-v2'", html)
                    self.assertLess(html.index('requestReload=requestReload'), html.index('async function paint()'))
            finally:
                app.server.shutdown()
                app.server.server_close()

    def test_binding_is_wired_before_start(self):
        text = SOURCE.read_text(encoding='utf-8-sig')
        self.assertLess(text.index('            bind_native_reload(app)'), text.index('            webview.start('))


if __name__ == '__main__':
    unittest.main()
