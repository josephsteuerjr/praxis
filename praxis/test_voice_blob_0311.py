"""Exercise Telethon's real conversion of the durable .blob spool."""
import atexit
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

_SESSION = tempfile.mkdtemp(prefix='praxis-voice-blob-session-')
atexit.register(shutil.rmtree, _SESSION, True)
os.environ.setdefault('TELEGRAM_API_ID', '1')
os.environ.setdefault('TELEGRAM_API_HASH', 'test')
os.environ.setdefault('PRAXIS_TEST', '1')
os.environ['TELEGRAM_SESSION'] = str(Path(_SESSION) / 'telethon')
from telethon.client.uploads import UploadMethods
from telethon.tl import types
import mtproto_runner as runner


class WireClient:
    _file_to_media = UploadMethods._file_to_media

    def __init__(self):
        self.requests = []

    async def upload_file(self, file, **kwargs):
        return types.InputFile(id=1, parts=1, name=Path(file).name, md5_checksum='')

    async def get_input_entity(self, entity):
        return types.InputPeerSelf()

    async def _parse_message_text(self, text, mode):
        return text, []

    async def __call__(self, request):
        self.requests.append(request)
        return SimpleNamespace(id=71)

    def _get_response_message(self, request, response, peer):
        return response


class VoiceBlobTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_telethon_conversion_retains_voice_mime_and_visible_name(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'outbox-private-hash.blob'
            path.write_bytes(b'OggS' + bytes(64))
            item = runner.media_core.OutboundMedia(
                kind='audio', path=path, mime='audio/ogg', size=path.stat().st_size,
                target_chat_id=42, scope='known', voice_note=True)
            client = WireClient()
            with patch.object(runner, 'client', client):
                sent, random_id = await runner._send_file_idempotent(
                    42, item, random_id=12345, visible_filename='voice.ogg', reply_to=19)
            self.assertEqual(sent.id, 71)
            self.assertEqual(random_id, 12345)
            request, = client.requests
            self.assertEqual(request.random_id, 12345)
            self.assertEqual(request.reply_to.reply_to_msg_id, 19)
            self.assertIsInstance(request.media, types.InputMediaUploadedDocument)
            self.assertEqual(request.media.mime_type, 'audio/ogg')
            attrs = {type(a): a for a in request.media.attributes}
            self.assertTrue(attrs[types.DocumentAttributeAudio].voice)
            self.assertEqual(attrs[types.DocumentAttributeFilename].file_name, 'voice.ogg')
            self.assertFalse(request.media.force_file)

    async def test_document_and_photo_keep_their_wire_types(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'private.blob'
            path.write_bytes(b'synthetic')
            for kind, mime, name in [('document', 'application/pdf', 'report.pdf'),
                                     ('photo', 'image/jpeg', 'photo.jpg')]:
                with self.subTest(kind=kind):
                    item = runner.media_core.OutboundMedia(
                        kind=kind, path=path, mime=mime, size=9, target_chat_id=42, scope='known')
                    client = WireClient()
                    with patch.object(runner, 'client', client):
                        await runner._send_file_idempotent(42, item, visible_filename=name)
                    media = client.requests[0].media
                    if kind == 'photo':
                        self.assertIsInstance(media, types.InputMediaUploadedPhoto)
                    else:
                        self.assertEqual(media.mime_type, mime)
                        self.assertTrue(media.force_file)
                        self.assertEqual(next(a.file_name for a in media.attributes
                                              if isinstance(a, types.DocumentAttributeFilename)), name)

    async def test_intent_receipt_does_not_claim_observed_voice(self):
        result = runner._direct_outbox_result({
            'kind': 'file', 'peer_id': 42,
            'receipt': {'message_id': 71, 'media_kind': 'audio', 'voice_note': True},
            'payload': {'visible_filename': 'voice.ogg', 'media_kind': 'audio', 'voice_note': True}})
        self.assertNotIn('Отправила голосовое', result)
        self.assertIn('запрошенный формат: голосовое', result)


if __name__ == '__main__':
    unittest.main()
