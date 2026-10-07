"""Real spool, run artifacts and provider image serialization; no network/model."""
import base64
from contextlib import nullcontext
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[2]
os.environ['PRAXIS_TEST'] = '1'
sys.path[:0] = [str(ROOT / 'helene/core'), str(ROOT / 'praxis')]
import _sandbox
assert _sandbox.activate_if_testing()
import agent
import media
import llm
from run_context import RunContext
from run_manager import RunManager
sys.path[:0] = [str(ROOT / 'desk/localharness'), str(ROOT / 'desk')]
import runner
import turn_inbox

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2XcAAAAASUVORK5CYII=')


class ActiveImages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # RunManager режет базы с симлинк-компонентами, а на macOS TMPDIR живёт
        # под /var → /private/var: канонизируем, иначе стенд мёртв на любом Mac
        # (упал в полном прогоне выпуска 1.3.3, run 37087943313).
        self.root = Path(self.tmp.name).resolve()
        self.inbox = self.root / 'memory/.control/desk_inbox'
        files = self.inbox / 'attachments/one'
        files.mkdir(parents=True)
        self.source = files / 'picture.png'
        self.source.write_bytes(PNG)
        self.spool = media.MediaSpool(self.root / 'spool')
        self.manager = RunManager(self.root)
        self.current = self.manager.create(RunContext.create(
            kind='chat_turn', goal='inspect image', principal_id='unknown',
            scope='owner', origin_chat_id='window', delivery_chat_id='window'), '# Test')
        self.patch(patch.object(runner, '_tree', self.root))
        self.patch(patch.object(runner, '_agent', agent))
        self.patch(patch.object(agent, '_media_spool', lambda: self.spool))
        self.patch(patch.object(agent, '_runs', lambda: self.manager))
        self.archived, self.done = [], []
        desk = SimpleNamespace(archive=lambda text, **kw: self.archived.append(text), life=lambda *a, **kw: None)
        self.r = SimpleNamespace(
            _tree=self.root, _speaker='owner', transport=SimpleNamespace(is_room=lambda r: r == 'window'),
            _inbox_target=lambda stem: 'window', _note_bytes=lambda p: p.read_bytes(),
            _seal_claim=lambda p, **kw: (True, ''), _message_text=runner._message_text,
            _split_attachments=runner._split_attachments, _hear_attachments=runner._hear_attachments,
            _IMAGE_EXT=runner._IMAGE_EXT, _batch_files=runner._batch_files,
            _batch_images=runner._batch_images, _room=lambda room: desk, _now=lambda: None,
            _mark_done=lambda p, n, why: (self.done.append(n), (p/(n+'.done')).write_text(why)))
        self.note = self.inbox / '001.md'
        self.note.write_text('Посмотри картинку\n[вложения]\n- attachments/one/picture.png · image/png · 68', encoding='utf8')

    def patch(self, context):
        context.start()
        self.addCleanup(context.stop)

    def png(self, rgb):
        """Настоящий 1×1 PNG заданного цвета: различимые пиксели без сети и без PIL."""
        def chunk(tag, data):
            return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
        ihdr = struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)
        return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr)
                + chunk(b'IDAT', zlib.compress(b'\x00' + bytes(rgb))) + chunk(b'IEND', b''))

    def add_picture(self, name, rgb):
        data = self.png(rgb)
        (self.inbox / 'attachments/one' / name).write_bytes(data)
        return data

    def note_with(self, files, text):
        lines = [f'- attachments/one/{name} · {mime} · '
                 f'{(self.inbox / "attachments/one" / name).stat().st_size}'
                 for name, mime in files]
        (self.inbox / '001.md').write_text(
            text + '\n[вложения]\n' + '\n'.join(lines), encoding='utf8')

    def test_pixels_reach_both_provider_payloads_and_survive_source_removal(self):
        batch, ack = turn_inbox.collect(self.r, self.current, [])
        blocks = batch[0]['content']
        picture = next(b for b in blocks if b['type'] == 'image')
        artifact = Path(picture['path'])
        self.assertTrue(artifact.is_relative_to(self.manager.path(self.current.run_id)))
        self.assertEqual(artifact.read_bytes(), PNG)
        self.assertTrue(self.source.exists(), 'precheckpoint replay still needs source')
        self.assertIn(str(artifact), self.archived[0])
        # Simulate cleanup of the original upload; the durable input still works.
        self.source.unlink()
        checkpoint = json.loads(json.dumps(agent._durable_model_messages(batch)))
        anthropic = llm.messages_to_anthropic(checkpoint)[0]['content']
        self.assertEqual(base64.b64decode(next(b['source']['data'] for b in anthropic if b['type']=='image')), PNG)
        openai = llm.messages_to_openai(checkpoint)[0]['content']
        url = next(b['image_url']['url'] for b in openai if b['type']=='image_url')
        self.assertEqual(base64.b64decode(url.split(',', 1)[1]), PNG)
        self.assertEqual(self.done, [])
        ack()
        self.assertEqual(self.done, ['001.md'])

    def test_checkpoint_replay_reuses_multimodal_message_not_sidecar(self):
        batch, _ = turn_inbox.collect(self.r, self.current, [])
        binding = self.inbox / 'processed/001.md.batch.json'
        record = json.loads(binding.read_text('utf8'))
        record['message']['content'] = [{'type':'image', 'path':'forged'}]
        binding.write_text(json.dumps(record), encoding='utf8')
        self.source.unlink()
        with patch.object(self.r, '_batch_images', side_effect=AssertionError('must reuse checkpoint')):
            more, ack = turn_inbox.collect(self.r, self.current, json.loads(json.dumps(batch)))
        self.assertEqual(more, [])
        ack()
        self.assertEqual(self.done, ['001.md'])

    def test_failed_durable_copy_is_not_acknowledged_and_source_survives(self):
        with patch.object(agent, '_archive_run_media', side_effect=RuntimeError('copy failed')):
            with self.assertRaisesRegex(RuntimeError, 'copy failed'):
                turn_inbox.collect(self.r, self.current, [])
        self.assertTrue(self.source.exists())
        self.assertTrue(self.note.exists())
        self.assertEqual(self.done, [])
        batch, _ = turn_inbox.collect(self.r, self.current, [])
        self.assertTrue(any(b['type']=='image' for b in batch[0]['content']))

    def test_missing_image_is_named_instead_of_claiming_vision(self):
        self.source.unlink()
        batch, _ = turn_inbox.collect(self.r, self.current, [])
        self.assertIsInstance(batch[0]['content'], str)
        self.assertIn('вложение не найдено', batch[0]['content'])

    def test_multiple_images_keep_order_and_pairing_in_both_provider_payloads(self):
        red = self.add_picture('first.png', (255, 0, 0))
        green = self.add_picture('second.png', (0, 255, 0))
        blue = self.add_picture('third.png', (0, 0, 255))
        self.note_with([('first.png', 'image/png'), ('second.png', 'image/png'),
                        ('third.png', 'image/png')], 'Сравни эти три по порядку')
        batch, _ = turn_inbox.collect(self.r, self.current, [])
        blocks = batch[0]['content']
        self.assertEqual(blocks[0]['type'], 'text')
        images = [b for b in blocks if b['type'] == 'image']
        self.assertEqual([Path(b['path']).read_bytes() for b in images], [red, green, blue])
        checkpoint = json.loads(json.dumps(agent._durable_model_messages(batch)))
        anthropic = [b for b in llm.messages_to_anthropic(checkpoint)[0]['content']
                     if b['type'] == 'image']
        self.assertEqual([base64.b64decode(b['source']['data']) for b in anthropic],
                         [red, green, blue])
        openai = [b for b in llm.messages_to_openai(checkpoint)[0]['content']
                  if b['type'] == 'image_url']
        self.assertEqual([base64.b64decode(b['image_url']['url'].split(',', 1)[1]) for b in openai],
                         [red, green, blue])

    def test_voice_and_image_in_one_note_deliver_words_and_pixels(self):
        picture = self.add_picture('snap.png', (10, 20, 30))
        (self.inbox / 'attachments/one/a.webm').write_bytes(b'synthetic media')
        self.note_with([('a.webm', 'audio/webm'), ('snap.png', 'image/png')], 'Глянь снимок')
        self.patch(patch.dict(sys.modules, media_audio=SimpleNamespace(
            transcribe=lambda p: 'сними показания со снимка')))
        self.patch(patch.object(runner, '_voice_state', {'ready': True}))
        self.patch(patch.object(runner, '_low_priority', nullcontext))
        batch, _ = turn_inbox.collect(self.r, self.current, [])
        blocks = batch[0]['content']
        self.assertEqual(blocks[0]['type'], 'text')
        self.assertIn('[голосовое]: сними показания со снимка', blocks[0]['text'])
        self.assertIn('Глянь снимок', blocks[0]['text'])
        image = next(b for b in blocks if b['type'] == 'image')
        self.assertEqual(Path(image['path']).read_bytes(), picture)
        self.assertIn('[голосовое]: сними показания со снимка', self.archived[0])
        self.assertIn('[изображение: ', self.archived[0])

    def test_turn_cap_overflow_names_dropped_images_instead_of_silent_cut(self):
        red = self.add_picture('first.png', (255, 0, 0))
        green = self.add_picture('second.png', (0, 255, 0))
        blue = self.add_picture('third.png', (0, 0, 255))
        self.note_with([('first.png', 'image/png'), ('second.png', 'image/png'),
                        ('third.png', 'image/png')], 'Все три важны')
        self.spool.max_turn_media = 2
        batch, _ = turn_inbox.collect(self.r, self.current, [])
        blocks = batch[0]['content']
        self.assertIn('вложений больше лимита хода: 3 > 2', blocks[0]['text'])
        # Спул даёт файлу своё имя с оригиналом в хвосте; для владельца важно,
        # что отрезанное названо, а не пропало молча.
        self.assertRegex(blocks[0]['text'], r'не показаны: .*first\.png')
        images = [b for b in blocks if b['type'] == 'image']
        self.assertEqual([Path(b['path']).read_bytes() for b in images], [green, blue])
        self.assertNotIn(red, [Path(b['path']).read_bytes() for b in images])
        # Отрезанное от модели всё равно архивируется и находимо в следующем ходе.
        self.assertEqual(self.archived[0].count('[изображение: '), 3)


if __name__ == '__main__':
    unittest.main()
