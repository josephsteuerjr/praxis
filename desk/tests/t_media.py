# -*- coding: utf-8 -*-
"""Вложение в ленте: что канал отдаёт байтами, а что — нет.

Запуск:  python tests/t_media.py

⚠ Главное здесь — что путь вложения приходит ИЗ СТРОКИ ЛЕНТЫ, а ленту пишет
агент. То есть это входящая строка, а не наша константа: `../../helene.json`
уехал бы владельцу вместе с ключом модели по первой же просьбе, если бы её
никто не проверял. Поэтому проверок три (корень, расширение, разрешённый путь),
и стенд стережёт каждую по отдельности.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from deskd import readers  # noqa: E402


class Ground(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Path(self.tmp.name)
        (self.tree / "media" / "tts").mkdir(parents=True)
        (self.tree / "memory" / ".state").mkdir(parents=True)
        self.wav = self.tree / "media" / "tts" / "tts-abc.wav"
        self.wav.write_bytes(b"RIFF....WAVE" + b"\0" * 100)
        # Секрет рядом — ровно то, чего отдавать нельзя ни под каким путём.
        (self.tree / "helene.json").write_text('{"model": {"key": "sk-live"}}',
                                               encoding="utf-8")
        real = readers.tree
        readers.tree = lambda: self.tree
        self.addCleanup(lambda: setattr(readers, "tree", real))


class WhatIsServed(Ground):
    def test_голос_из_дерева_отдаётся(self):
        path, ctype, why = readers.media_file("media/tts/tts-abc.wav")
        self.assertEqual(path, self.wav.resolve())
        self.assertEqual(ctype, "audio/wav")
        self.assertEqual(why, "")

    def test_вложение_из_ящика_записок_тоже(self):
        box = self.tree / "memory" / ".control" / "desk_inbox" / "attachments" / "2026"
        box.mkdir(parents=True)
        shot = box / "снимок.png"
        shot.write_bytes(b"\x89PNG\r\n")
        path, ctype, _ = readers.media_file("memory/.control/desk_inbox/attachments/2026/снимок.png")
        self.assertEqual(path, shot.resolve())
        self.assertEqual(ctype, "image/png")

    def test_чего_нет_говорится_словами(self):
        path, _, why = readers.media_file("media/tts/нет-такого.wav")
        self.assertIsNone(path)
        self.assertIn("нет", why)


class WhatIsRefused(Ground):
    def test_выход_вверх_по_дереву_закрыт(self):
        """⚠ Самый дорогой случай: `helene.json` — это ключ модели и токен бота."""
        for said in ("../helene.json", "media/../helene.json",
                     "media/tts/../../helene.json", "/etc/passwd", "C:/Windows/win.ini"):
            path, _, why = readers.media_file(said)
            self.assertIsNone(path, f"{said} не должен отдаваться")
            self.assertTrue(why)

    def test_чужой_корень_дерева_закрыт(self):
        """Память, конституция и настройки — не вложения, и путь туда закрыт
        целиком, а не по расширениям."""
        (self.tree / "memory" / "life").mkdir(parents=True, exist_ok=True)
        boo = self.tree / "memory" / "life" / "events.png"
        boo.write_bytes(b"\x89PNG\r\n")
        path, _, why = readers.media_file("memory/life/events.png")
        self.assertIsNone(path)
        self.assertIn("вложения живут", why)

    def test_чужое_расширение_закрыто(self):
        code = self.tree / "media" / "утечка.py"
        code.write_text("print('секрет')", encoding="utf-8")
        path, _, why = readers.media_file("media/утечка.py")
        self.assertIsNone(path)
        self.assertIn(".py", why)

    def test_пустой_путь_не_отдаёт_дерево(self):
        path, _, why = readers.media_file("")
        self.assertIsNone(path)
        self.assertTrue(why)

    def test_ссылка_наружу_не_проходит(self):
        """Проверка строки ссылку не видит; поэтому путь сверяется и ПОСЛЕ
        разрешения — уже как файл."""
        outside = Path(self.tmp.name).parent / "чужое.wav"
        outside.write_bytes(b"RIFF")
        link = self.tree / "media" / "ссылка.wav"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("символические ссылки в этой системе не заводятся")
        path, _, why = readers.media_file("media/ссылка.wav")
        self.assertIsNone(path, "ссылка наружу отдаваться не должна")
        self.assertIn("вне дерева", why)
        outside.unlink(missing_ok=True)


class ChannelDoor(unittest.TestCase):
    def test_ручка_есть_и_телефону_туда_можно(self):
        import deskapp
        paths = {r.path for r in deskapp.ROUTES}
        self.assertIn("/api/media", paths)
        # Телефон показывает ту же переписку: голос в ней — та же реплика, звуком.
        self.assertIn("/api/media", deskapp._DEVICE_PATHS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
