"""On Windows a header or edge drag goes to the system's own move/size loop (2.8.7).

Tim (Windows 10, 17.11): "окно не успевает за движением курсора". pywebview moved the
frameless window from the renderer, one SetWindowPos per mousemove; the owner's 2.8.6 window
showed 50-77 ms stalls of WebView2's browser thread while dragging. begin_native_drag posts
WM_NCLBUTTONDOWN with the caption/edge hit-test code on the window's own thread instead.
This checks the shell contract with fakes; the feel of the drag is physical acceptance.
"""
import ctypes
from pathlib import Path
import sys
import types
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import python_app


class Native:
    def __init__(self):
        self.posted = []

    def BeginInvoke(self, action):
        self.posted.append(action)


class User32:
    def __init__(self):
        self.calls = []
        self.PostMessageW = self._post

    def GetCursorPos(self, point):
        target = point._obj
        target.x, target.y = -30, 700  # a monitor left of the primary one
        return 1

    def ReleaseCapture(self):
        self.calls.append(('release',))
        return 1

    def _post(self, hwnd, msg, wparam, lparam):
        self.calls.append(('post', hwnd.value, msg, wparam.value, lparam.value))
        return 1


class NativeDrag(unittest.TestCase):
    def app(self, maximized=False, handle=4242):
        return SimpleNamespace(window=SimpleNamespace(native=Native()), window_handle=handle,
                               window_maximized=maximized)

    def run_on_windows(self, app, args):
        user32 = User32()
        system = types.ModuleType('System')
        system.Action = lambda fn: fn
        with patch.object(python_app.sys, 'platform', 'win32'), \
                patch.dict(sys.modules, {'System': system}), \
                patch.object(ctypes, 'windll', SimpleNamespace(user32=user32), create=True), \
                patch.object(python_app, 'window_is_maximized', lambda a: a.window_maximized):
            result = python_app.begin_native_drag(app, args)
            for action in (app.window.native.posted if app.window else []):
                action()  # what the window thread runs
        return result, user32.calls

    def test_caption_and_every_edge_post_their_hit_test_code(self):
        expected = {'move': 2, 'w': 10, 'e': 11, 'n': 12, 'nw': 13, 'ne': 14, 's': 15, 'sw': 16, 'se': 17}
        self.assertEqual(python_app.NATIVE_DRAG_HIT, expected)
        self.assertEqual(set(expected) - {'move'}, set(python_app.RESIZE_EDGES))
        for edge, hit in expected.items():
            app = self.app()
            result, calls = self.run_on_windows(app, {'edge': edge})
            self.assertTrue(result)
            # Capture released first, then the press posted (not sent: the bridge returns).
            self.assertEqual(calls[0], ('release',))
            self.assertEqual(calls[1][:4], ('post', 4242, 0x00A1, hit))
            self.assertEqual(calls[1][4], ((-30) & 0xFFFF) | (700 << 16))

    def test_default_is_the_caption(self):
        result, calls = self.run_on_windows(self.app(), {})
        self.assertTrue(result)
        self.assertEqual(calls[1][3], 2)

    def test_maximized_unknown_or_handleless_windows_stay_put(self):
        for app, args in ((self.app(maximized=True), {'edge': 'move'}),
                          (self.app(), {'edge': 'middle'}), (self.app(), {'edge': 7}),
                          (self.app(handle=0), {'edge': 'move'}),
                          (SimpleNamespace(window=None, window_handle=1), {'edge': 'move'})):
            result, calls = self.run_on_windows(app, args)
            self.assertFalse(result)
            self.assertEqual(calls, [])

    def test_other_platforms_keep_the_renderer_drag(self):
        for platform in ('linux', 'darwin'):
            with patch.object(python_app.sys, 'platform', platform):
                self.assertFalse(python_app.begin_native_drag(self.app(), {'edge': 'move'}))

    def test_renderer_reaches_it_through_python_window(self):
        app = object.__new__(python_app.Application)
        app.root = app.program = Path(__file__).resolve().parent
        app.window = None
        self.assertFalse(app.invoke('python_window', {'action': 'begin_drag', 'edge': 'move'}))

    def test_pywebview_renderer_drag_is_off_only_on_windows(self):
        source = (Path(__file__).resolve().parents[1] / 'python_app.py').read_text('utf-8')
        self.assertIn("(NATIVE_DRAG_OFF if sys.platform == 'win32'", source)
        main = (Path(__file__).resolve().parents[1] / 'ui-kit/window/main.ts').read_text('utf-8')
        self.assertIn(f'const header = "{python_app.HEADER_DRAG_SELECTOR}"', main)


if __name__ == '__main__':
    unittest.main()
