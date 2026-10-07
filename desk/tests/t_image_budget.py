# -*- coding: utf-8 -*-
"""Бюджет изображений на запрос по ногам: последние N + честные маркеры.

Числа бюджета — из живых проб glm-5.3-flash (3 стабильно; 10 — развал) и слова
владельца для openai-ноги (10, подписка кодекса закончилась). Здесь проверяется
механика: отрезаются СТАРЫЕ, отрезанное названо маркером в ленте, сверхлимитный
по размеру файл не едет молча, живой запрос не зовётся (фейки в _TEST_CLIENTS).
"""
import base64
import os
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[2]
os.environ['PRAXIS_TEST'] = '1'
sys.path[:0] = [str(ROOT / 'helene/core'), str(ROOT / 'praxis')]
import _sandbox
assert _sandbox.activate_if_testing()
import llm


def png(rgb, size=8):
    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data)))
    ihdr = struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0)
    row = b'\x00' + bytes(rgb) * size
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr)
            + chunk(b'IDAT', zlib.compress(row * size)) + chunk(b'IEND', b''))


def image_block(tmp, name, rgb, size=8):
    path = Path(tmp) / name
    path.write_bytes(png(rgb, size))
    return {"type": "image", "path": str(path), "mime": "image/png", "detail": "auto"}


class Budget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mark = llm._IMAGE_BUDGET_MARKER.format(framework='anthropic', kept=3)

    def messages_with(self, blocks_per_message):
        out = []
        n = 0
        for blocks in blocks_per_message:
            content = [{"type": "text", "text": f"реплика {n}"}]
            for rgb in blocks:
                n += 1
                content.append(image_block(self.tmp.name, f"p{n}.png", rgb))
            out.append({"role": "user", "content": content})
        return out

    def images(self, messages):
        return [b for m in messages for b in m.get("content", [])
                if isinstance(b, dict) and b.get("type") == "image"]

    def markers(self, messages):
        return [b for m in messages for b in m.get("content", [])
                if isinstance(b, dict) and b.get("type") == "text"
                and "request budget keeps" in str(b.get("text"))]

    def test_anthropic_keeps_last_three_and_marks_the_rest(self):
        msgs = self.messages_with([[(255, 0, 0)] * 2, [(0, 255, 0)] * 3])  # 5 в двух репликах
        out = llm._sighted_messages("anthropic", msgs)
        self.assertEqual(len(self.images(out)), 3)
        self.assertEqual(len(self.markers(out)), 2)
        # Остались последние: обе картинки второй реплики и хвост первой.
        kept = [Path(b["path"]).name for b in self.images(out)]
        self.assertEqual(kept, ["p3.png", "p4.png", "p5.png"])
        self.assertIn(self.mark, [b["text"] for b in self.markers(out)])

    def test_oversize_file_is_marked_not_sent(self):
        big = Path(self.tmp.name) / "big.png"
        big.write_bytes(b"\x00" * (llm.IMAGE_REQUEST_MAX_BYTES + 1))
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "глянь"},
            {"type": "image", "path": str(big), "mime": "image/png", "detail": "auto"},
            image_block(self.tmp.name, "small.png", (0, 0, 255))]}]
        out = llm._sighted_messages("anthropic", msgs)
        self.assertEqual(len(self.images(out)), 1)
        self.assertEqual(Path(self.images(out)[0]["path"]).name, "small.png")
        self.assertEqual(len(self.markers(out)), 1)

    def test_three_ride_untrimmed_on_anthropic(self):
        msgs = self.messages_with([[(255, 0, 0), (0, 255, 0), (0, 0, 255)]])
        out = llm._sighted_messages("anthropic", msgs)
        self.assertEqual(len(self.images(out)), 3)
        self.assertEqual(self.markers(out), [])

    def test_openai_budget_is_ten(self):
        msgs = self.messages_with([[(10 * (i,)) and (i % 256, (i * 40) % 256, (i * 80) % 256)
                                    for i in range(12)]])
        out = llm._sighted_messages("openai", msgs)
        self.assertEqual(len(self.images(out)), 10)
        self.assertEqual(len(self.markers(out)), 2)
        kept = [Path(b["path"]).name for b in self.images(out)]
        self.assertEqual(kept[0], "p3.png")  # первые две отрезаны


class ChatWiring(unittest.TestCase):
    """Сквозной вызов: бюджет применяется до провода, фейк видит итоговую ленту."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="praxis_imgbudget_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self._orig = [(llm, k, getattr(llm, k)) for k in ("CONFIG_PATH", "JOURNAL_DIR")]
        llm.CONFIG_PATH = self.tmp / "llm.json"
        llm.JOURNAL_DIR = self.tmp / "journal"
        llm._CACHE.update(mtime=None, cfg=None)
        llm.clear_test_clients()
        self._prepass = os.environ.pop(llm.VISION_PREPASS_LEVER, None)

        cfg = llm._from_env()
        cfg["frameworks"]["anthropic"]["api_key"] = "zai-key"
        cfg["roles"]["voice"].update(framework="anthropic", model="glm-5.3-flash")
        llm.save_config(cfg)

        class Resp:
            stop_reason = "end_turn"
            content = [types.SimpleNamespace(type="text", text="ок")]
            usage = types.SimpleNamespace(input_tokens=5, output_tokens=1)
            model = "glm-5.3-flash"
        calls = []

        def create(**kw):
            calls.append(kw)
            return Resp()
        self.calls = calls
        self.fake = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
        llm.use_test_client(self.fake, "anthropic")

    def tearDown(self):
        for mod, k, v in self._orig:
            setattr(mod, k, v)
        llm._CACHE.update(mtime=None, cfg=None)
        llm.clear_test_clients()
        if self._prepass is not None:
            os.environ[llm.VISION_PREPASS_LEVER] = self._prepass

    def test_chat_marks_dropped_images_before_wire(self):
        content = [{"type": "text", "text": "сравни"}]
        for i, rgb in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255), (240, 140, 30)]):
            path = self.tmp / f"c{i}.png"
            path.write_bytes(png(rgb))
            content.append({"type": "image", "path": str(path),
                            "mime": "image/png", "detail": "auto"})
        response = llm.chat("voice", messages=[{"role": "user", "content": content}])
        self.assertEqual(response.text, "ок")
        wire = self.calls[0]["messages"][0]["content"]
        sent = [b for b in wire if b.get("type") == "image"]
        marked = [b for b in wire if b.get("type") == "text"
                  and "request budget keeps" in b.get("text", "")]
        self.assertEqual(len(sent), 3, "anthropic-нога везёт ровно бюджет 3")
        self.assertEqual(len(marked), 1)


if __name__ == "__main__":
    unittest.main()
