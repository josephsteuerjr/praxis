"""The setup icon input must invalidate its Windows PE resource build."""
from __future__ import annotations

import json
from pathlib import Path
import struct
import unittest

DESK = Path(__file__).resolve().parents[1]


class SetupIconBuild(unittest.TestCase):
    def test_windows_icon_is_a_tracked_build_input_and_contains_small_frames(self):
        config = json.loads((DESK / 'setup' / 'tauri.conf.json').read_text(encoding='utf8'))
        self.assertIn('icons/icon.ico', config['bundle']['icon'])
        script = (DESK / 'setup' / 'build.rs').read_text(encoding='utf8')
        self.assertIn('cargo:rerun-if-changed=icons/icon.ico', script)
        setup = (DESK / 'setup' / 'icons' / 'icon.ico').read_bytes()
        self.assertEqual(setup, (DESK / 'shell' / 'icons' / 'icon.ico').read_bytes())
        reserved, kind, count = struct.unpack_from('<HHH', setup)
        self.assertEqual((reserved, kind), (0, 1))
        frames = {}
        for i in range(count):
            w, h, _, _, _, _, length, offset = struct.unpack_from('<BBBBHHII', setup, 6 + i * 16)
            self.assertEqual(w or 256, h or 256)
            frame = setup[offset:offset + length]
            self.assertEqual(len(frame), length)
            self.assertTrue(frame.startswith(b'\x89PNG\r\n\x1a\n'))
            frames[w or 256] = frame
        self.assertEqual(sorted(frames), [16, 24, 32, 48, 64, 128, 256])
        self.assertNotEqual(frames[16], frames[24])


if __name__ == '__main__':
    unittest.main(verbosity=2)
