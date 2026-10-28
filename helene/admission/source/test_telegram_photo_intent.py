"""Exercise the real Telethon converter without uploading or sending bytes."""
import atexit
import os
import shutil
import tempfile
import types
import unittest
from pathlib import Path

_SESSION = tempfile.mkdtemp(prefix="praxis-photo-intent-")
atexit.register(shutil.rmtree, _SESSION, True)
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "test")
os.environ["TELEGRAM_SESSION"] = str(Path(_SESSION) / "telethon")
os.environ.setdefault("PRAXIS_TEST", "1")

from PIL import Image
from telethon.client.uploads import UploadMethods
from telethon.tl import types as tl
from unittest.mock import patch
import media
import mtproto_runner as runner


class OfflineTelethon:
    _file_to_media = UploadMethods._file_to_media

    def __init__(self):
        self.requests = []

    async def upload_file(self, file, **kwargs):
        return tl.InputFile(1, 1, "payload.blob", "")

    async def get_input_entity(self, entity):
        return tl.InputPeerUser(42, 1)

    async def _parse_message_text(self, text, mode):
        return text, []

    async def __call__(self, request):
        self.requests.append(request)
        return None

    def _get_response_message(self, request, response, entity):
        return types.SimpleNamespace(id=91)


class PhotoIntentTests(unittest.IsolatedAsyncioTestCase):
    async def test_photo_blob_keeps_photo_on_first_send_and_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.blob"
            Image.new("RGB", (12, 8), "blue").save(path, format="PNG")
            item = media.OutboundMedia(kind="photo", path=path, mime="image/png",
                                       size=path.stat().st_size, target_chat_id=42,
                                       scope="known", queue_id="same-photo")
            client = OfflineTelethon()
            with patch.object(runner, "client", client):
                first = await runner._send_file_idempotent(42, item, random_id=123)
                replay = await runner._send_file_idempotent(42, item, random_id=123)
            self.assertEqual(first[1], replay[1])
            self.assertEqual(len(client.requests), 2)
            for request in client.requests:
                self.assertIsInstance(request.media, tl.InputMediaUploadedPhoto)
                self.assertEqual(request.random_id, 123)

    async def test_explicit_document_with_image_extension_stays_document(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "original.png"
            Image.new("RGB", (12, 8), "blue").save(path)
            item = media.OutboundMedia(kind="document", path=path, mime="image/png",
                                       size=path.stat().st_size, target_chat_id=42,
                                       scope="known")
            client = OfflineTelethon()
            with patch.object(runner, "client", client):
                await runner._send_file_idempotent(42, item, visible_filename="original.png")
            sent = client.requests[0].media
            self.assertIsInstance(sent, tl.InputMediaUploadedDocument)
            self.assertEqual(sent.mime_type, "image/png")
            self.assertIn("original.png", [a.file_name for a in sent.attributes
                                           if isinstance(a, tl.DocumentAttributeFilename)])


if __name__ == "__main__":
    unittest.main()
