# -*- coding: utf-8 -*-
"""Папка хода: произвольные файлы вкладывания копируются в runs/<id>/files.

Слово владельца 01.10: вложения — просто создание папки во время хода, с
артефактами, в которой он работает. Здесь проверяется контракт целиком:
приём неизвестного типа окном, копия в папку прогона, путь словами в реплике,
идемпотентность до checkpoint, честный отказ без прогона,DeskApp-граница.
"""
import base64
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'desk'), str(ROOT / 'desk/localharness')]
import runner
import turn_inbox


class TurnFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inbox = self.root / 'memory/.control/desk_inbox'
        files = self.inbox / 'attachments/one'
        files.mkdir(parents=True)
        self.source = files / 'report.pdf'
        self.source.write_bytes(b'%PDF-1.4 fake')
        (files / 'notes.txt').write_text('заметки', encoding='utf8')
        self.archived, self.done = [], []
        desk = SimpleNamespace(archive=lambda text, **kw: self.archived.append(text),
                               life=lambda *a, **kw: None)
        self.r = SimpleNamespace(
            _tree=self.root, _speaker='owner',
            transport=SimpleNamespace(is_room=lambda r: r == 'window'),
            _inbox_target=lambda stem: 'window', _note_bytes=lambda p: p.read_bytes(),
            _seal_claim=lambda p, **kw: (True, ''), _message_text=runner._message_text,
            _split_attachments=runner._split_attachments, _hear_attachments=runner._hear_attachments,
            _IMAGE_EXT=runner._IMAGE_EXT, _batch_files=runner._batch_files,
            _batch_images=lambda text, paths, **kw: (text, text),
            _room=lambda room: desk, _now=lambda: None,
            _mark_done=lambda p, n, why: (self.done.append(n), (p / (n + '.done')).write_text(why)))
        self.run_root = self.root / 'runs'
        manager = SimpleNamespace(path=lambda run_id: self.run_root / run_id)
        self.agent = SimpleNamespace(_runs=lambda: manager)
        self.patch(patch.object(runner, '_tree', self.root))
        self.patch(patch.object(runner, '_agent', self.agent))
        self.current = SimpleNamespace(run_id='run-20261001T000000Z-test',
                                       delivery_chat_id='window')

    def patch(self, context):
        context.start()
        self.addCleanup(context.stop)

    def note(self, text='Вот отчёт и заметки', files=('report.pdf', 'notes.txt')):
        lines = [f'- attachments/one/{n} · application/octet-stream · '
                 f'{(self.inbox / "attachments/one" / n).stat().st_size}' for n in files]
        (self.inbox / '001.md').write_text(
            text + '\n[вложения]\n' + '\n'.join(lines), encoding='utf8')

    def test_files_land_in_run_folder_and_are_named_by_path(self):
        self.note()
        batch, _ = turn_inbox.collect(self.r, self.current, [])
        content = batch[0]['content']
        self.assertIsInstance(content, str, 'без картинок реплика остаётся текстом')
        folder = self.run_root / self.current.run_id / 'files'
        self.assertEqual((folder / 'report.pdf').read_bytes(), b'%PDF-1.4 fake')
        self.assertEqual((folder / 'notes.txt').read_text(encoding='utf8'), 'заметки')
        self.assertIn('[файл хода:', content)
        self.assertIn('report.pdf', content)
        # Исходник в inbox жив до ACK: replay до checkpoint требует source.
        self.assertTrue(self.source.exists())
        self.assertIn('report.pdf', self.archived[0])

    def test_precheckpoint_retry_is_idempotent(self):
        self.note()
        turn_inbox.collect(self.r, self.current, [])
        target = self.run_root / self.current.run_id / 'files/report.pdf'
        stamp = target.stat().st_mtime_ns
        _batch, ack = turn_inbox.collect(self.r, self.current, [])
        self.assertEqual(target.stat().st_mtime_ns, stamp, 'повтор не переписывает файл')
        self.assertEqual(target.read_bytes(), b'%PDF-1.4 fake')
        ack()
        self.assertEqual(self.done, ['001.md'])

    def test_missing_file_is_named_not_silent(self):
        self.note(files=('report.pdf',))
        self.source.unlink()
        batch, _ = turn_inbox.collect(self.r, self.current, [])
        self.assertIn('файл не найден: report.pdf', batch[0]['content'])

    def test_without_durable_run_files_are_named_honestly(self):
        self.note()
        broken = SimpleNamespace(_runs=lambda: (_ for _ in ()).throw(RuntimeError('no runs')))
        with patch.object(runner, '_agent', broken), patch.object(runner, '_tree', self.root):
            batch, _ = turn_inbox.collect(self.r, self.current, [])
        self.assertIn('папка хода недоступна', batch[0]['content'])
        self.assertIn('report.pdf', batch[0]['content'])


class DeskappAcceptsAnyFile(unittest.TestCase):
    """Граница окна: неизвестный тип принимается, имя защищено, лимиты прежние."""

    def test_unknown_mime_gets_extension_from_name(self):
        import deskapp
        raw = [{"name": "проект смета.xlsx", "mime": "application/vnd.ms-excel",
                "data": base64.b64encode(b'xlsx-bytes').decode()}]
        (item,) = deskapp._attachments_in(raw)
        self.assertEqual(item['name'], 'проект_смета.xlsx')
        self.assertEqual(item['mime'], 'application/vnd.ms-excel')

    def test_unknown_mime_without_extension_gets_bin(self):
        import deskapp
        raw = [{"name": "Makefile", "mime": "text/plain",
                "data": base64.b64encode(b'all:').decode()}]
        (item,) = deskapp._attachments_in(raw)
        self.assertEqual(item['name'], 'Makefile.bin')

    def test_known_image_contract_unchanged(self):
        import deskapp
        raw = [{"name": "shot", "mime": "image/png",
                "data": base64.b64encode(b'png').decode()}]
        (item,) = deskapp._attachments_in(raw)
        self.assertEqual(item['name'], 'shot.png')


if __name__ == "__main__":
    unittest.main()
