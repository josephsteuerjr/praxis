"""Offline regressions against real installed media module; no Telegram/model I/O."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'localharness'))
module_path = Path(os.environ.get('MEDIA_AUDIT_MODULE', APP / 'localharness/telegram_media.py'))
spec = importlib.util.spec_from_file_location('audit_telegram_media', module_path)
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)


class Client:
    def __init__(self, data=b'media-bytes'):
        self.data = data
        self.downloads = 0

    def download_media(self, message, destination):
        self.downloads += 1
        Path(destination).write_bytes(self.data)


def photo():
    return {'message_id': 9, 'chat': {'id': -42}, 'photo': [
        {'file_id': 'small', 'file_unique_id': 'small-u', 'width': 10, 'height': 10},
        {'file_id': 'large', 'file_unique_id': 'large-u', 'width': 100, 'height': 100}]}


class MediaAuditTests(unittest.TestCase):
    def test_photo_largest_persist_preview_and_topic_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            client = Client()
            self.assertEqual(media.item(photo())[1]['file_id'], 'large')
            first = media.persist(client, tree, '-42:100', photo())
            second = media.persist(client, tree, '-42:200', photo())
            self.assertEqual(first, second)
            self.assertEqual(client.downloads, 1)
            self.assertEqual(first['media_kind'], 'image')
            self.assertEqual(first['media_preview_path'], first['media_path'])
            self.assertEqual((tree / first['media_path']).read_bytes(), client.data)

    def test_voice_and_audio_are_audio_without_fake_visual_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            for kind in ('voice', 'audio'):
                message = {'message_id': 1, kind: {'file_id': kind, 'file_unique_id': kind,
                           'mime_type': 'audio/ogg', 'file_size': 3}}
                result = media.persist(Client(b'ogg'), Path(tmp), '42', message)
                self.assertEqual(result['media_kind'], 'audio')
                self.assertEqual(result['media_mime'], 'audio/ogg')
                self.assertNotIn('media_preview_path', result)

    def test_empty_and_oversized_media_leave_no_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            with self.assertRaises(ValueError):
                media.persist(Client(b''), tree, '42', photo())
            self.assertFalse(list(tree.rglob('*.part')))
            self.assertFalse(list(tree.rglob('*.jpg')))
            message = photo()
            message['photo'][-1]['file_size'] = media.LIMIT + 1
            client = Client()
            with self.assertRaises(ValueError):
                media.persist(client, tree, '42', message)
            self.assertEqual(client.downloads, 0)

    def test_actual_download_over_limit_is_rejected_without_declared_size(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp, patch.object(media, 'LIMIT', 4):
            with self.assertRaises(ValueError):
                media.persist(Client(b'12345'), Path(tmp), '42', photo())
            self.assertFalse(list(Path(tmp).rglob('*.part')))
            self.assertFalse(list(Path(tmp).rglob('*.jpg')))

    def test_failed_download_leaves_no_partial_file(self):
        class Broken(Client):
            def download_media(self, message, destination):
                Path(destination).write_bytes(b'partial')
                raise OSError('simulated connection drop')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OSError):
                media.persist(Broken(), Path(tmp), '42', photo())
            self.assertFalse(list(Path(tmp).rglob('*.part')))
            self.assertFalse(list(Path(tmp).rglob('*.jpg')))

    def test_concurrent_same_photo_downloads_have_independent_temps(self):
        # Both downloads enter before either may commit. A shared .part path
        # guarantees one caller fails (or deletes the other's in-flight bytes).
        barrier = threading.Barrier(2)
        destinations = []
        class Concurrent(Client):
            def download_media(self, message, destination):
                destinations.append(Path(destination))
                Path(destination).write_bytes(self.data)
                barrier.wait(timeout=5)
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs = [pool.submit(media.persist, Concurrent(), tree, peer, photo())
                        for peer in ('-42:100', '-42:200')]
                results = [job.result(timeout=10) for job in jobs]
            self.assertNotEqual(destinations[0], destinations[1])
            self.assertEqual(results[0], results[1])
            self.assertEqual((tree / results[0]['media_path']).read_bytes(), b'media-bytes')
            self.assertFalse(list(tree.rglob('*.part')))

    def test_concurrent_thumbnail_downloads_have_independent_temps(self):
        barrier = threading.Barrier(2)
        destinations = []
        class ThumbnailClient(Client):
            def download_file(self, file_id, destination):
                destinations.append(Path(destination))
                Path(destination).write_bytes(b'jpeg-preview')
                barrier.wait(timeout=5)
        message = {'message_id': 1, 'video': {'file_id': 'video',
                   'file_unique_id': 'video-u', 'mime_type': 'video/mp4',
                   'thumbnail': {'file_id': 'thumb'}}}
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            # Populate the complete video, but not its thumbnail, before racing.
            without_thumb = {'message_id': 1, 'video': dict(message['video'])}
            without_thumb['video'].pop('thumbnail')
            media.persist(Client(), tree, '42', without_thumb)
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs = [pool.submit(media.persist, ThumbnailClient(), tree, '42', message)
                        for _ in range(2)]
                results = [job.result(timeout=10) for job in jobs]
            self.assertNotEqual(destinations[0], destinations[1])
            for result in results:
                self.assertEqual((tree / result['media_preview_path']).read_bytes(), b'jpeg-preview')
            self.assertFalse(list(tree.rglob('*.part')))

    def test_publish_permission_error_only_accepts_identical_complete_cache(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'target.jpg'
            temp = Path(tmp) / 'unique.part'
            temp.write_bytes(b'complete')
            target.write_bytes(b'complete')
            with patch.object(media.os, 'replace', side_effect=PermissionError('busy')):
                media._publish(temp, target)
                target.write_bytes(b'different')
                with self.assertRaises(PermissionError):
                    media._publish(temp, target)
                target.unlink()
                with self.assertRaises(PermissionError):
                    media._publish(temp, target)

    def test_deferred_recovery_preserves_topic_and_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            message = photo()
            media.defer(tree, '-42:200', message)
            path = tree / 'memory/.state/telegram-media-pending.json'
            jobs = media.package_notice.read(path)
            jobs['-42:200:9']['retry_at'] = 0
            media.package_notice.write(path, jobs)
            calls = []
            class Rooms:
                def attach_media(self, *args):
                    calls.append(args)
                    return True
            media.recover(tree, Client(), Rooms())
            self.assertEqual(calls[0][:3], ('-42:200', '9', media.source(message)))
            self.assertEqual(calls[0][3]['media_kind'], 'image')
            self.assertEqual(media.package_notice.read(path), {})


class AudioConfigurationTests(unittest.TestCase):
    def test_disabled_stt_has_no_launch_environment(self):
        import voice
        from unittest.mock import patch
        with patch.object(voice, 'stt_state', return_value={'ready': False}):
            self.assertEqual(voice.env_for(Path('unused'), {}), {})

    def test_ready_stt_uses_local_model_cpu_and_reserves_one_core(self):
        import voice
        from unittest.mock import patch
        state = {'ready': True, 'installed': {'path': 'local-model'}}
        with patch.object(voice, 'stt_state', return_value=state), patch.object(voice.os, 'cpu_count', return_value=4):
            env = voice.env_for(Path('unused'), {'voice': {'threads': 99}})
        self.assertEqual(env['PRAXIS_STT_MODEL'], 'local-model')
        self.assertEqual(env['PRAXIS_STT_LOCAL_FILES_ONLY'], '1')
        self.assertEqual(env['PRAXIS_STT_DEVICE'], 'cpu')
        self.assertEqual(env['PRAXIS_STT_CPU_THREADS'], '3')
        self.assertEqual(env['PRAXIS_STT_KEEP_LOADED'], '0')


if __name__ == '__main__':
    unittest.main(verbosity=2)
