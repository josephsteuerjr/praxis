"""Style tests; --native/--manual open only an isolated blank pywebview window.

Manual acceptance (parent only): active taskbar click -> minimize -> next click
restore; maximize and repeat; X -> tray Open; tray Exit. No profile or agent.
"""
import ctypes
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import python_tray


class Styles(unittest.TestCase):
    def test_preserves_unrelated_bits_and_does_not_activate(self):
        for before in (0x16010000, 0x360b0000, 0x170b0000):
            api = SimpleNamespace(GetWindowLongW=Mock(return_value=before),
                SetWindowLongW=Mock(return_value=before), SetWindowPos=Mock(return_value=1))
            form = SimpleNamespace(Handle=SimpleNamespace(ToInt64=lambda: 0x123456789))
            with patch.object(ctypes, 'WinDLL', return_value=api, create=True), patch.object(ctypes, 'set_last_error', create=True):
                after = python_tray._enable_taskbar_minimize(form)
            self.assertEqual(after, before | 0xa0000)
            if after == before:
                api.SetWindowLongW.assert_not_called()
                api.SetWindowPos.assert_not_called()
            else:
                api.SetWindowLongW.assert_called_once_with(0x123456789, -16, after)
                api.SetWindowPos.assert_called_once_with(0x123456789, None, 0, 0, 0, 0, 0x37)

    def test_invalid_handle_raises(self):
        api = SimpleNamespace(GetWindowLongW=Mock(return_value=0), SetWindowLongW=Mock(), SetWindowPos=Mock())
        form = SimpleNamespace(Handle=SimpleNamespace(ToInt64=lambda: 123))
        with patch.object(ctypes, 'WinDLL', return_value=api, create=True), patch.object(ctypes, 'set_last_error', create=True), patch.object(ctypes, 'get_last_error', return_value=6, create=True), patch.object(ctypes, 'WinError', return_value=OSError('invalid handle'), create=True):
            with self.assertRaisesRegex(OSError, 'invalid handle'):
                python_tray._enable_taskbar_minimize(form)


def native_fixture(manual=False):
    if sys.platform != 'win32': raise RuntimeError('Windows native fixture only')
    import webview
    import webview.platforms.winforms  # load actual WinForms assembly
    from System import Action
    from System.Reflection import BindingFlags
    u = ctypes.WinDLL('user32', use_last_error=True)
    u.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u.GetWindowLongW.restype = ctypes.c_long
    u.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    u.GetWindow.restype = ctypes.c_void_p
    u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    u.SendMessageW.restype = ctypes.c_ssize_t
    rows, errors = [], []
    # Native WebView caches are run evidence, never source/patch payloads.
    evidence = Path(__file__).resolve().parents[2] / '.ouroboros'
    evidence.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='native-taskbar-', dir=evidence) as folder:
        window = webview.create_window('Helene taskbar fixture 15b69aff', html='<html><body style="background:#1d1914;color:#e5d7b8;font:20px system-ui;padding:32px"><h1>Taskbar fixture</h1><p>Isolated blank window. No agent or profile.</p><p>Active taskbar click: minimize, then restore.<br>Maximize and repeat. X to tray, Open, Exit.</p></body></html>', width=720, height=400, frameless=True, easy_drag=False)
        app = SimpleNamespace(window=window, program=Path(folder), tray=None, exiting=False, relaunch=False, event=lambda name, data: print(name, data, flush=True))
        def ui(fn): return window.native.Invoke(Action(fn))
        def snapshot(label):
            def read():
                f = window.native; h = f.Handle.ToInt64()
                row = dict(phase=label, hwnd=h, style=hex(u.GetWindowLongW(h,-16)&0xffffffff), exstyle=hex(u.GetWindowLongW(h,-20)&0xffffffff), owner=u.GetWindow(h,4), state=str(f.WindowState), visible=bool(f.Visible), taskbar=bool(f.ShowInTaskbar))
                rows.append(row); print(json.dumps(row), flush=True)
            ui(read)
        def message(command):
            ui(lambda: u.SendMessageW(window.native.Handle.ToInt64(), 0x112, command, 0))
            time.sleep(.25)
        def exercise():
            try:
                time.sleep(.5); snapshot('frameless-before')
                python_tray.install(app); snapshot('installed')
                if '--capture' in sys.argv:
                    from System.Drawing import Bitmap, Graphics
                    from System.Drawing.Imaging import ImageFormat
                    def capture():
                        form = window.native
                        image = Bitmap(form.Width, form.Height)
                        graphics = Graphics.FromImage(image)
                        hdc = graphics.GetHdc()
                        try:
                            u.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
                            u.PrintWindow.restype = ctypes.c_int
                            if not u.PrintWindow(form.Handle.ToInt64(), hdc.ToInt64(), 2):
                                raise RuntimeError('PrintWindow failed')
                            image.Save(str(evidence / 'taskbar-native-capture.png'), ImageFormat.Png)
                        finally:
                            graphics.ReleaseHdc(hdc); graphics.Dispose(); image.Dispose()
                    ui(capture)
                if manual:
                    for _ in range(180):
                        if app.exiting: return
                        time.sleep(1); snapshot('manual')
                else:
                    for command, label, expected in [(0xf020,'minimize','Minimized'),(0xf120,'restore','Normal'),(0xf030,'maximize','Maximized'),(0xf020,'maximized-minimize','Minimized'),(0xf120,'maximized-restore','Maximized')]:
                        message(command); snapshot(label)
                        assert rows[-1]['state']==expected, rows[-1]
                    ui(window.native.Hide); snapshot('hide')
                    ui(window.native.Show); snapshot('show')
                    assert rows[-1]['state']=='Maximized' and rows[-1]['visible']
                    message(0xf020)
                    ui(lambda: app.tray.ContextMenuStrip.Items[0].PerformClick())
                    time.sleep(.25); snapshot('tray-open-from-minimized-maximized')
                    assert rows[-1]['state']=='Maximized'
                    ui(lambda: window.native.GetType().GetMethod('RecreateHandle', BindingFlags.Instance | BindingFlags.NonPublic).Invoke(window.native, None))
                    time.sleep(.25)
                    snapshot('handle-recreated')
                    assert int(rows[-1]['style'],16)&0xa0000==0xa0000
                    assert rows[-1]['owner'] is None and rows[-1]['taskbar']
            except BaseException as exc:
                errors.append(repr(exc)); print('NATIVE_ERROR', repr(exc), flush=True)
            finally:
                if not app.exiting:
                    app.exiting=True; window.destroy()
        window.events.shown += exercise
        webview.start(gui='edgechromium', private_mode=True, storage_path=folder)
    if errors: raise AssertionError(errors)
    print('NATIVE_STYLE_MESSAGE_PASS; Explorer taskbar click NOT exercised', flush=True)


if __name__=='__main__':
    if '--native' in sys.argv or '--manual' in sys.argv: native_fixture('--manual' in sys.argv)
    else: unittest.main()
