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


if __name__ == '__main__':
    unittest.main()
