# -*- coding: utf-8 -*-
"""Вложения из окна (0.5.0): канал -> подвал записки -> разбор руннером.

Запуск:  python tests/t_inbox_attachments.py

Контракт в трёх звеньях:
  * deskapp._attachments_in — принимает только картинки, которые читает модель,
    до четырёх и до 8 МБ, и называет отказ словами (HTTP 400), а не молчит;
  * deskapp._write_attachments — кладёт файлы атомарно в
    desk_inbox/attachments/<stamp>/ и отдаёт пути для подвала `[вложения]`;
  * runner._split_attachments — отделяет подвал от реплики владельца и не пускает
    путь наружу из attachments/ (никаких `..`).
"""
from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "localharness"))

import deskapp  # noqa: E402
import runner  # noqa: E402
from aiohttp import web  # noqa: E402

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode("ascii")


class AttachmentsIn(unittest.TestCase):
    def test_accepts_images_and_names_them(self):
        files = deskapp._attachments_in([
            {"name": "снимок экрана.png", "mime": "image/png", "data": PNG},
            {"name": "", "mime": "image/jpeg", "data": PNG},
        ])
        self.assertEqual([f["name"] for f in files], ["снимок_экрана.png", "image2.jpg"])
        self.assertEqual(files[0]["mime"], "image/png")
        self.assertTrue(files[0]["data"].startswith(b"\x89PNG"))

    def test_empty_is_no_attachments(self):
        self.assertEqual(deskapp._attachments_in(None), [])
        self.assertEqual(deskapp._attachments_in([]), [])

    def test_refuses_what_the_model_cannot_read(self):
        with self.assertRaises(web.HTTPBadRequest) as cm:
            deskapp._attachments_in([{"name": "a.pdf", "mime": "application/pdf", "data": PNG}])
        self.assertIn("не читается моделью", cm.exception.text)
        with self.assertRaises(web.HTTPBadRequest):
            deskapp._attachments_in([{"name": "a.png", "mime": "image/png", "data": "not base64!"}])
        with self.assertRaises(web.HTTPBadRequest):
            deskapp._attachments_in([{"name": "a.png", "mime": "image/png", "data": PNG}] * 5)
        big = base64.b64encode(b"\x00" * (8 * 1024 * 1024 + 1)).decode("ascii")
        with self.assertRaises(web.HTTPBadRequest) as cm:
            deskapp._attachments_in([{"name": "a.png", "mime": "image/png", "data": big}])
        self.assertIn("8 МБ", cm.exception.text)


class WriteAndSplit(unittest.TestCase):
    def test_files_land_and_the_footer_parses_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            control = Path(tmp) / "desk_inbox"
            files = deskapp._attachments_in([
                {"name": "a.png", "mime": "image/png", "data": PNG},
                {"name": "a.png", "mime": "image/png", "data": PNG},   # одноимённые не затирают друг друга
            ])
            rel = deskapp._write_attachments(control, "20260909T000000000000Z", files)
            self.assertEqual(rel, ["attachments/20260909T000000000000Z/a.png",
                                   "attachments/20260909T000000000000Z/a-2.png"])
            for r in rel:
                self.assertTrue((control / r).is_file(), r)
            self.assertFalse(list(control.rglob(".part-*")), "временных файлов не осталось")
            footer = "\n[вложения]\n" + "".join(
                f"- {r} · {f['mime']} · {len(f['data'])}\n" for r, f in zip(rel, files))
            text, paths = runner._split_attachments("посмотри, что тут\n" + footer)
            self.assertEqual(text, "посмотри, что тут")
            self.assertEqual(paths, rel)

    def test_footer_only_message_is_an_empty_word_with_files(self):
        text, paths = runner._split_attachments("[вложения]\n- attachments/x/a.png · image/png · 10\n")
        self.assertEqual(text, "")
        self.assertEqual(paths, ["attachments/x/a.png"])

    def test_no_footer_no_paths_and_no_escape(self):
        self.assertEqual(runner._split_attachments("просто текст\n\nбез подвала"),
                         ("просто текст\n\nбез подвала", []))
        text, paths = runner._split_attachments(
            "x\n\n[вложения]\n- attachments/../../soul/SOUL.md · image/png · 1\n- /etc/passwd · image/png · 1\n")
        self.assertEqual(text, "x")
        self.assertEqual(paths, [])


class _Spool:
    """Медиа-спул дерева в одном методе: тот же контракт `ingest_path(move=True)`."""

    def __init__(self, root: Path):
        self.root = root
        self.calls: list[dict] = []

    def ingest_path(self, source, *, kind, chat_id, message_id, scope, move=False, caption=""):
        self.calls.append({"kind": kind, "chat_id": chat_id, "scope": scope, "move": move})
        if kind != "photo":
            raise ValueError(f"unsupported kind {kind}")
        self.root.mkdir(parents=True, exist_ok=True)
        dest = self.root / Path(source).name
        dest.write_bytes(Path(source).read_bytes())
        if move:
            Path(source).unlink()
        return type("Ref", (), {"path": dest, "kind": kind, "mime": "image/png"})()


class RunnerIntake(unittest.TestCase):
    """Что руннер делает с записками, у которых есть подвал вложений."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = Path(self.tmp.name)
        self.inbox = self.tree / "memory" / ".control" / "desk_inbox"
        (self.inbox / "attachments" / "st1").mkdir(parents=True)
        (self.inbox / "attachments" / "st1" / "кот.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        self.spool = _Spool(self.tree / "memory" / "media" / "inbound")
        self.saved = {k: getattr(runner, k) for k in ("_tree", "_agent")}
        runner._tree = self.tree
        runner._agent = type("A", (), {"_media_spool": staticmethod(lambda: self.spool)})()

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(runner, k, v)
        self.tmp.cleanup()

    def test_picture_reaches_the_spool_as_owner_media(self):
        refs, notes = runner._ingest_attachments(["attachments/st1/кот.png"],
                                                 chat_id="window", message_id="window-1")
        self.assertEqual(notes, [])
        self.assertEqual(len(refs), 1)
        self.assertEqual(self.spool.calls[0], {"kind": "photo", "chat_id": "window",
                                               "scope": "owner", "move": True})
        self.assertTrue((self.tree / "memory" / "media" / "inbound" / "кот.png").is_file())
        self.assertFalse((self.inbox / "attachments" / "st1" / "кот.png").exists(),
                         "файл перенесён в спул, а не скопирован")

    def test_missing_or_foreign_file_is_named_not_swallowed(self):
        refs, notes = runner._ingest_attachments(["attachments/st1/нет.png"],
                                                 chat_id="window", message_id="window-2")
        self.assertEqual(refs, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("не найдено", notes[0])

    def test_without_a_tree_nothing_happens(self):
        runner._tree = None
        self.assertEqual(runner._ingest_attachments(["attachments/st1/кот.png"],
                                                    chat_id="window", message_id="x"), ([], []))


if __name__ == "__main__":
    unittest.main()
