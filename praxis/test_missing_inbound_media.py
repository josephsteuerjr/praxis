"""Missing historical spool bytes must not erase a new text-only turn."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import agent
import media
import run_manager


class MissingInboundMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spool = media.MediaSpool(self.root / 'spool')
        self.manager = run_manager.RunManager(self.root / 'runs')
        self.ctx = agent.ChannelContext(chat_id='777', owner=True)
        self.ref = self.spool.ingest_bytes(
            b'\xff\xd8\xff\xe0fixture', kind='photo', filename='old.jpg',
            scope='owner', chat_id='777', message_id=11)
        for name, value in (('_MEDIA_SPOOL', self.spool), ('_RUN_MANAGER', self.manager)):
            p = patch.object(agent, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_expired_historical_photo_keeps_current_text_and_provenance(self):
        removed = self.spool.cleanup(now=self.ref.path.stat().st_mtime + 100, ttl_seconds=1)
        self.assertIn(self.ref.path, removed)
        # Stop at the actual model seam, after the real strict artifact archive.
        with patch.object(agent.llm, 'configured', return_value=True), \
                patch.object(agent, '_presence_frame', return_value=''), \
                patch.object(agent, '_presence_evidence', return_value=''), \
                patch.object(agent, '_voice', side_effect=RuntimeError('model seam')) as voice:
            result = agent.voice_turn_envelope(
                '777', 'historical photo\nnew text', ctx=self.ctx, media_refs=(self.ref,),
                history=[{'role': 'assistant', 'content': 'earlier answer'}],
                current_text='new text')
        voice.assert_called_once()
        content = voice.call_args.args[0]
        self.assertIsInstance(content, str)
        self.assertIn('new text', content)
        self.assertIn('недоступно', content)
        self.assertNotIn('historical photo', content)
        context = (self.manager.path(result.run_id) / 'context.md').read_text()
        self.assertIn('недоступно', context)
        events = self.manager.events(result.run_id)
        missing = [e for e in events if e['kind'] == 'inbound_media_unavailable']
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]['attachments'][0]['sha256'], self.ref.sha256)
        self.assertEqual(missing[0]['attachments'][0]['message_id'], 11)
        self.assertFalse(list((self.manager.path(result.run_id) / 'artifacts').glob('inbound-*')))

    def _run_to_model_seam(self, refs):
        # Keep prompt validation, run creation/evidence and strict archival real.
        with patch.object(agent.llm, 'configured', return_value=True), \
                patch.object(agent, '_presence_frame', return_value=''), \
                patch.object(agent, '_presence_evidence', return_value=''), \
                patch.object(agent, '_voice', side_effect=RuntimeError('model seam')) as voice:
            result = agent.voice_turn_envelope(
                '777', 'historical photo\nnew text', ctx=self.ctx, media_refs=refs,
                history=[{'role': 'assistant', 'content': 'earlier answer'}],
                current_text='new text')
        return result, voice

    def _assert_failed_before_voice(self, result, voice, reason):
        voice.assert_not_called()
        self.assertTrue(result.failed)
        self.assertTrue(result.run_id)
        manifest = self.manager.manifest(result.run_id)
        self.assertEqual(manifest['status'], 'failed')
        self.assertIn('inbound media was not made durable', manifest['terminal']['reason'])
        self.assertIn(reason, manifest['terminal']['reason'])

    def test_mixed_missing_and_available_archives_only_available_for_model(self):
        self.ref.path.unlink()
        available = self.spool.ingest_bytes(
            b'\xff\xd8\xff\xe0available', kind='photo', filename='available.jpg',
            scope='owner', chat_id='777', message_id=12)
        with patch.object(self.manager, 'store_artifact',
                          wraps=self.manager.store_artifact) as store:
            result, voice = self._run_to_model_seam((self.ref, available))
        voice.assert_called_once()
        content = voice.call_args.args[0]
        self.assertIsInstance(content, list)
        text = [block['text'] for block in content if block['type'] == 'text']
        self.assertEqual(len(text), 1)
        self.assertIn('new text', text[0])
        self.assertIn('недоступно', text[0])
        self.assertNotIn('historical photo', text[0])
        images = [block for block in content if block['type'] == 'image']
        self.assertEqual(len(images), 1)
        durable_path = Path(images[0]['path'])
        self.assertNotEqual(durable_path, available.path)
        self.assertEqual(durable_path.parent, self.manager.path(result.run_id) / 'artifacts')
        self.assertEqual(durable_path.read_bytes(), available.path.read_bytes())
        inbound_calls = [call for call in store.call_args_list
                         if call.kwargs.get('name', '').startswith('inbound-')]
        self.assertEqual(len(inbound_calls), 1)
        self.assertEqual(inbound_calls[0].args[1], available.path)
        artifacts = list((self.manager.path(result.run_id) / 'artifacts').glob('*inbound-*'))
        self.assertEqual(artifacts, [durable_path])
        missing = [e for e in self.manager.events(result.run_id)
                   if e['kind'] == 'inbound_media_unavailable']
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]['reason'], 'local_file_missing')
        self.assertEqual(missing[0]['attachments'], [{
            'kind': self.ref.kind, 'message_id': self.ref.message_id,
            'sha256': self.ref.sha256, 'size': self.ref.size,
        }])

    def test_available_archive_failure_prevents_voice(self):
        store_artifact = self.manager.store_artifact

        def fail_inbound_archive(*args, **kwargs):
            if kwargs.get('name', '').startswith('inbound-'):
                raise OSError('inbound archive write failed')
            return store_artifact(*args, **kwargs)

        with patch.object(self.manager, 'store_artifact',
                          side_effect=fail_inbound_archive) as store:
            result, voice = self._run_to_model_seam((self.ref,))
        self._assert_failed_before_voice(result, voice, 'inbound archive write failed')
        self.assertTrue(any(call.args[1] == self.ref.path for call in store.call_args_list))
        self.assertTrue(self.ref.path.is_file())

    def test_missing_evidence_append_failure_prevents_voice(self):
        self.ref.path.unlink()
        append_event = self.manager.append_event

        def fail_missing_evidence(run_id, kind, **kwargs):
            if kind == 'inbound_media_unavailable':
                raise OSError('missing evidence write failed')
            return append_event(run_id, kind, **kwargs)

        with patch.object(self.manager, 'append_event', side_effect=fail_missing_evidence) as append, \
                patch.object(agent, '_archive_run_media', wraps=agent._archive_run_media) as archive:
            result, voice = self._run_to_model_seam((self.ref,))
        self._assert_failed_before_voice(result, voice, 'missing evidence write failed')
        attempted = [call for call in append.call_args_list
                     if call.args[1] == 'inbound_media_unavailable']
        self.assertEqual(len(attempted), 1)
        self.assertEqual(attempted[0].kwargs['attachments'][0]['sha256'], self.ref.sha256)
        archive.assert_not_called()
        self.assertFalse(any(e['kind'] == 'inbound_media_unavailable'
                             for e in self.manager.events(result.run_id)))

    def test_deleted_after_prompt_validation_fails_strict_archive_before_voice(self):
        archive_media = agent._archive_run_media

        def delete_before_archive(run_id, items, **kwargs):
            self.assertTrue(kwargs['strict'])
            self.assertEqual(tuple(items), (self.ref,))
            self.ref.path.unlink()
            return archive_media(run_id, items, **kwargs)

        with patch.object(self.spool, 'validate_ref', wraps=self.spool.validate_ref) as validate, \
                patch.object(agent, '_archive_run_media', side_effect=delete_before_archive) as archive:
            result, voice = self._run_to_model_seam((self.ref,))
        validate.assert_any_call(self.ref, expected_scope=self.ctx.scope,
                                 expected_chat_id=self.ctx.chat_id)
        archive.assert_called_once()
        self._assert_failed_before_voice(result, voice, 'run media is unavailable before model call')
        self.assertFalse(any(e['kind'] == 'inbound_media_unavailable'
                             for e in self.manager.events(result.run_id)))
        self.assertFalse(list((self.manager.path(result.run_id) / 'artifacts').glob('*inbound-*')))

    def test_only_missing_can_fallback(self):
        self.ref.path.unlink()
        unavailable = []
        receipt, content = agent._media_prompt('new', (self.ref,), self.ctx,
                                                unavailable_refs=unavailable)
        self.assertIn('недоступно', receipt)
        self.assertEqual(unavailable, [self.ref])
        for ref in (replace(self.ref, chat_id='other'), replace(self.ref, scope='group'),
                    replace(self.ref, sha256='bad')):
            with self.subTest(ref=ref), self.assertRaises(media.MediaValidationError):
                agent._media_prompt('new', (ref,), self.ctx)

    def test_changed_bytes_not_text_fallback(self):
        self.ref.path.write_bytes(b'\xff\xd8\xff\xe0CHANGED')
        with self.assertRaises(media.MediaValidationError):
            agent._media_prompt('new', (self.ref,), self.ctx)

    def test_permission_failure_not_text_fallback(self):
        with patch.object(Path, 'stat', side_effect=PermissionError('denied')):
            with self.assertRaises((PermissionError, media.MediaSecurityError)):
                agent._media_prompt('new', (self.ref,), self.ctx)

    def test_missing_symlink_not_text_fallback(self):
        self.ref.path.unlink()
        self.ref.path.symlink_to(self.root / 'absent.jpg')
        with self.assertRaises(media.MediaSecurityError):
            agent._media_prompt('new', (self.ref,), self.ctx)

    def test_available_photo_is_still_forwarded(self):
        _, content = agent._media_prompt('new', (self.ref,), self.ctx)
        self.assertEqual(content[1]['path'], str(self.ref.path))
