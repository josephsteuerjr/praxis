"""The frameless pywebview window resizes from every edge with the opposite side fixed.

Reported on Windows 10 (2560x1080, 100%): the window edges could not be dragged. pywebview's
frameless WinForms/Qt window has no native resize border, so the renderer's handles send the
requested size and the shell resizes with a fix point. This checks the shell contract with a
fake window; physical acceptance on the reporting machine remains separate.
"""
import enum
from pathlib import Path
import sys
import types
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import python_app


class FixPoint(enum.Flag):
    NORTH = enum.auto()
    WEST = enum.auto()
    EAST = enum.auto()
    SOUTH = enum.auto()


class Window:
    def __init__(self):
        self.calls = []

    def resize(self, width, height, fix_point):
        self.calls.append((width, height, fix_point))


class WindowResize(unittest.TestCase):
    def setUp(self):
        module = types.ModuleType('webview.window')
        module.FixPoint = FixPoint
        self.enterContext(patch.dict(sys.modules, {'webview.window': module}))

    def app(self, maximized=False):
        return SimpleNamespace(window=Window(), window_maximized=maximized)

    def test_each_edge_fixes_the_opposite_side(self):
        expected = {
            'e': FixPoint.NORTH | FixPoint.WEST, 's': FixPoint.NORTH | FixPoint.WEST,
            'se': FixPoint.NORTH | FixPoint.WEST, 'w': FixPoint.NORTH | FixPoint.EAST,
            'sw': FixPoint.NORTH | FixPoint.EAST, 'n': FixPoint.SOUTH | FixPoint.WEST,
            'ne': FixPoint.SOUTH | FixPoint.WEST, 'nw': FixPoint.SOUTH | FixPoint.EAST}
        self.assertEqual(set(expected), set(python_app.RESIZE_EDGES))
        for edge, fix in expected.items():
            app = self.app()
            self.assertTrue(python_app.resize_edge(app, {'edge': edge, 'width': 1200, 'height': 800}))
            self.assertEqual(app.window.calls, [(1200, 800, fix)], edge)

    def test_minimum_size_is_kept(self):
        app = self.app()
        python_app.resize_edge(app, {'edge': 'se', 'width': 10, 'height': 10})
        self.assertEqual(app.window.calls[0][:2], python_app.MIN_WINDOW_SIZE)

    def test_maximized_absent_or_malformed_requests_change_nothing(self):
        app = self.app(maximized=True)
        self.assertFalse(python_app.resize_edge(app, {'edge': 'e', 'width': 900, 'height': 700}))
        self.assertEqual(app.window.calls, [])
        self.assertFalse(python_app.resize_edge(SimpleNamespace(window=None),
                                                {'edge': 'e', 'width': 900, 'height': 700}))
        for bad in ({'edge': 'x', 'width': 900, 'height': 700},
                    {'edge': 'e', 'width': 900.5, 'height': 700},
                    {'edge': 'e', 'width': True, 'height': 700},
                    {'edge': 'e', 'width': 0, 'height': 700},
                    {'edge': 'e', 'width': 900, 'height': 10 ** 9}, {}):
            app = self.app()
            self.assertFalse(python_app.resize_edge(app, bad), bad)
            self.assertEqual(app.window.calls, [])


class MaximizedWindowKeepsItsEdges(unittest.TestCase):
    """2.8.2 (owner, 16.11): resize "worked even maximized" and the screen edge was a strip.

    The maximized state is the window's own (IsZoomed), not pywebview's event mirror; a
    maximized window refuses edge resizes and header moves, and the renderer can ask.
    """

    def setUp(self):
        module = types.ModuleType('webview.window')
        module.FixPoint = FixPoint
        self.enterContext(patch.dict(sys.modules, {'webview.window': module}))

    def zoomed(self, value):
        user32 = SimpleNamespace(IsZoomed=lambda handle: value)
        return patch.object(python_app.sys, 'platform', 'win32'), patch('ctypes.windll', SimpleNamespace(user32=user32), create=True)

    def test_windows_answer_comes_from_the_window_not_the_event_mirror(self):
        for zoomed, mirror in ((1, False), (0, True)):
            app = SimpleNamespace(window=Window(), window_handle=0x2906, window_maximized=mirror)
            platform, windll = self.zoomed(zoomed)
            with platform, windll:
                self.assertIs(python_app.window_is_maximized(app), bool(zoomed))
                changed = python_app.resize_edge(app, {'edge': 'e', 'width': 900, 'height': 700})
            self.assertIs(changed, not zoomed)
            self.assertEqual(len(app.window.calls), 0 if zoomed else 1)

    def test_without_a_native_handle_the_mirror_decides(self):
        self.assertTrue(python_app.window_is_maximized(SimpleNamespace(window=Window(), window_maximized=True)))
        self.assertFalse(python_app.window_is_maximized(SimpleNamespace(window=Window())))
        self.assertFalse(python_app.window_is_maximized(SimpleNamespace(window=None, window_maximized=True)))

    def test_header_drag_does_not_move_a_maximized_window(self):
        moves = []
        window = SimpleNamespace(move=lambda x, y: moves.append((x, y)))
        app = SimpleNamespace(window=window, window_maximized=True)
        python_app.guard_maximized_move(app)
        window.move(40, 50)
        self.assertEqual(moves, [])
        app.window_maximized = False
        window.move(40, 50)
        self.assertEqual(moves, [(40, 50)])

    def test_bind_window_state_installs_the_move_guard(self):
        class Hook:
            def __iadd__(self, handler):
                return self
        moves = []
        window = SimpleNamespace(move=lambda x, y: moves.append((x, y)),
                                 events=SimpleNamespace(maximized=Hook(), restored=Hook(), shown=Hook(), closing=Hook()))
        app = SimpleNamespace(window=window, window_maximized=True)
        python_app.bind_window_state(app)
        window.move(1, 2)
        self.assertEqual(moves, [])

    def test_renderer_reads_the_state_through_python_window(self):
        app = object.__new__(python_app.Application)
        app.root = app.program = Path(__file__).resolve().parent
        app.window = Window()
        app.window_maximized = True
        self.assertEqual(app.invoke('python_window', {'action': 'state'}), {'maximized': True})
        app.window_maximized = False
        self.assertEqual(app.invoke('python_window', {'action': 'state'}), {'maximized': False})
        app.window = None
        self.assertEqual(app.invoke('python_window', {'action': 'state'}), {'maximized': False})


class HandlesInstallForTheBridgePlatform(unittest.TestCase):
    """2.8.0/2.8.1 shipped a guard against "win32" while the bridge says "windows".

    Found by the owner's agent on his own Windows machine (16.11): the drag changed nothing
    because no handle existed. The renderer's list must name the bridge's actual words.
    """

    def test_renderer_list_names_the_bridge_platform_words(self):
        from localharness import python_platform
        source = (Path(__file__).resolve().parents[1] / 'ui-kit/window/native-resize.ts').read_text('utf-8')
        import re
        match = re.search(r'NATIVE_RESIZE_PLATFORMS[^=]*=\s*\[([^\]]*)\]', source)
        self.assertIsNotNone(match)
        listed = set(re.findall(r'"([^"]+)"', match.group(1)))
        reported = set()
        for internal in ('win32', 'linux'):
            with patch.object(python_platform.sys, 'platform', internal):
                reported.add(python_platform.desktop_identity()['platform'])
        self.assertEqual(reported, {'windows', 'linux'})
        self.assertEqual(listed, reported)
        with patch.object(python_platform.sys, 'platform', 'darwin'):
            self.assertNotIn(python_platform.desktop_identity()['platform'], listed)
        main = (Path(__file__).resolve().parents[1] / 'ui-kit/window/main.ts').read_text('utf-8')
        self.assertIn('NATIVE_RESIZE_PLATFORMS.includes(String(bridge.platform))', main)
        self.assertNotIn('"win32"', main[main.index('native-resize') - 400:main.index('native-resize') + 400])


if __name__ == '__main__':
    unittest.main()
