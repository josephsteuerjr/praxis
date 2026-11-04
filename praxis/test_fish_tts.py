"""Offline Fish producer acceptance. No sockets, subprocesses, real keys or sends."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import urllib.error

from fish_tts_client import API, FishError, FishProfile, FishTTSClient, _NoRedirect, opus_seconds, read_key
from xtts_generations import Generations, process_owner
from xtts_protocol import XttsError


def ogg_page(body, *, sequence, flags, granule=0):
    # Framing fixture, deliberately not claimed as decodable speech.
    assert len(body) < 255
    return (b'OggS\x00' + bytes([flags]) + struct.pack('<QII', granule, 123, sequence)
            + bytes(4) + b'\x01' + bytes([len(body)]) + body)


def ogg_fixture():
    return (ogg_page(b'OpusHead' + bytes([1, 1]) + struct.pack('<HIhB', 312, 48000, 0, 0),
                     sequence=0, flags=2)
            + ogg_page(b'OpusTags' + bytes(8), sequence=1, flags=0)
            + ogg_page(b'fixture-packet', sequence=2, flags=4, granule=48312))


class Response(io.BytesIO):
    pass


class FishCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='praxis-fish-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.key = self.base / 'api-key'
        self.key.write_text('offline-test-token-never-a-real-key', encoding='ascii')
        self.key.chmod(0o600)
        self.profile = FishProfile('offline-test-voice')
        self.transport = mock.Mock()
        self.raw = ogg_fixture()
        self.transport.open.side_effect = lambda *a, **kw: Response(self.raw)
        self.client = FishTTSClient(self.base, self.profile, key_file=self.key,
                                    opener=self.transport)
        self.store = Generations(self.base / 'generations')
        self.job = self.store.submit('Привет, Праксис! [whisper] Слышишь?',
            run_id='offline-run', call_id='offline-call',
            channel={'chat_id': 'offline-origin', 'scope': 'owner', 'is_dm': True},
            selection=self.profile.selection())

    def test_exact_speech_and_only_required_provider_data_leave_the_boundary(self):
        result = self.store.execute(self.job['generation_id'], self.client, owner=process_owner())
        self.assertEqual(result['state'], 'ready')
        request = self.transport.open.call_args.args[0]
        self.assertEqual(request.full_url, API + '/v1/tts')
        self.assertEqual(request.get_header('Model'), self.profile.model)
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + self.key.read_text())
        body = json.loads(request.data)
        self.assertEqual(body['text'], self.job['request']['text'])
        self.assertEqual(body['reference_id'], self.profile.reference_id)
        self.assertEqual(body['format'], 'opus')
        self.assertEqual(body['opus_bitrate'], 48000)
        self.assertTrue({'run_id', 'request_id', 'target_chat_id', 'scope', 'api_key'}.isdisjoint(body))
        audio = self.store.audio_path(self.job['generation_id'])
        self.assertEqual(audio.read_bytes(), self.raw)
        self.assertEqual(result['result']['sha256'], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(result['result']['audio_seconds'], 1)
        observation = self.store.observation(result)
        self.assertEqual(observation['delivery'], 'not_requested_by_generation')

    def test_replaying_a_completed_generation_never_charges_again(self):
        for _ in range(2):
            self.store.execute(self.job['generation_id'], self.client, owner=process_owner())
        self.assertEqual(self.transport.open.call_count, 1)

    def test_changed_profile_is_rejected_before_post(self):
        self.client.profile = replace(self.profile, speed=1.1)
        result = self.store.execute(self.job['generation_id'], self.client, owner=process_owner())
        self.assertEqual((result['state'], result['error']), ('failed', 'profile_changed'))
        self.transport.open.assert_not_called()

    def test_model_typo_cannot_silently_fall_back_to_a_paid_model(self):
        with self.assertRaisesRegex(FishError, 'fish_invalid_model'):
            FishProfile('voice', model='s2.1-pro-fre')

    def test_voice_must_be_selected_explicitly(self):
        with self.assertRaisesRegex(FishError, 'fish_voice_not_selected'):
            FishProfile('')

    def test_equivalent_speed_has_same_identity_but_new_voice_does_not(self):
        self.assertEqual(FishProfile('v', speed=1).profile_id, FishProfile('v', speed=1.0).profile_id)
        self.assertNotEqual(FishProfile('v').profile_id, FishProfile('other').profile_id)
        self.assertNotEqual(self.profile.model_id, replace(self.profile, model='drama-3').model_id)

    def test_existing_audio_is_preserved_without_post(self):
        audio = self.store.audio_path(self.job['generation_id'])
        audio.write_bytes(b'previous-audio')
        with self.assertRaisesRegex(FishError, 'generation_conflict'):
            self.client.stream_intent(self.job['request'], audio)
        self.assertEqual(audio.read_bytes(), b'previous-audio')
        self.transport.open.assert_not_called()

    def test_a_racing_output_creator_is_not_deleted_or_charged(self):
        destination = self.base / 'race.ogg'
        original = os.open

        def racing(path, flags, *args):
            if Path(path) == destination:
                destination.write_bytes(b'racing-owner')
            return original(path, flags, *args)

        with mock.patch('fish_tts_client.os.open', side_effect=racing):
            with self.assertRaisesRegex(FishError, 'generation_conflict'):
                self.client.stream_intent(self.job['request'], destination)
        self.assertEqual(destination.read_bytes(), b'racing-owner')
        self.transport.open.assert_not_called()

    def test_rejected_credentials_and_remote_body_are_not_exposed(self):
        self.transport.open.side_effect = urllib.error.HTTPError(API, 401, 'private-body', {},
            io.BytesIO(('private-speech ' + self.key.read_text()).encode()))
        result = self.store.execute(self.job['generation_id'], self.client, owner=process_owner())
        self.assertEqual((result['state'], result['error']), ('failed', 'unauthorized'))
        serialized = json.dumps(self.store.observation(result))
        self.assertNotIn(self.key.read_text(), serialized)
        self.assertNotIn('private-speech', serialized)
        self.assertFalse(self.store.audio_path(self.job['generation_id']).exists())

    def test_empty_paid_balance_does_not_select_a_different_model(self):
        self.transport.open.side_effect = urllib.error.HTTPError(API, 402, 'no credit', {}, io.BytesIO())
        result = self.store.execute(self.job['generation_id'], self.client, owner=process_owner())
        self.assertEqual((result['state'], result['error']), ('failed', 'fish_no_api_credit'))
        self.assertEqual(self.transport.open.call_count, 1)
        self.assertEqual(self.transport.open.call_args.args[0].get_header('Model'), 's2.1-pro')

    def test_network_or_server_failure_is_unknown_and_not_automatically_retried(self):
        for failure in (TimeoutError(), urllib.error.URLError('offline'),
                        urllib.error.HTTPError(API, 503, 'unavailable', {}, io.BytesIO())):
            with self.subTest(error=type(failure).__name__):
                job = self.store.submit('Тест', run_id='offline-run', call_id=type(failure).__name__,
                    channel=self.job['channel'], selection=self.profile.selection())
                self.transport.open.reset_mock()
                self.transport.open.side_effect = failure
                result = self.store.execute(job['generation_id'], self.client, owner=process_owner())
                self.assertEqual(result['state'], 'unknown')
                self.store.execute(job['generation_id'], self.client, owner=process_owner())
                self.assertEqual(self.transport.open.call_count, 1)

    def test_interrupted_response_is_unknown_and_partial_audio_removed(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = [self.raw[:30], http.client.IncompleteRead(b'partial')]
        self.transport.open.side_effect = None
        self.transport.open.return_value = response
        result = self.store.execute(self.job['generation_id'], self.client, owner=process_owner())
        self.assertEqual(result['state'], 'unknown')
        self.assertFalse(self.store.audio_path(self.job['generation_id']).exists())

    def test_invalid_or_truncated_ogg_is_never_ready(self):
        for data in (b'JSON error instead of audio', self.raw[:-1], self.raw + b'trailing'):
            with self.subTest(length=len(data)):
                destination = self.base / 'invalid.ogg'
                self.transport.open.side_effect = lambda *a, **kw: Response(data)
                with self.assertRaisesRegex(FishError, 'invalid_audio'):
                    self.client.stream_intent(self.job['request'], destination)
                self.assertFalse(destination.exists())

    def test_cancellation_stops_local_consumption_without_claiming_remote_cancel(self):
        case = self

        class Cancelling(Response):
            def read(self, size=-1):
                case.store.cancel(case.job['generation_id'])
                return super().read(size)

        self.transport.open.side_effect = lambda *a, **kw: Cancelling(self.raw)
        result = self.store.execute(self.job['generation_id'], self.client, owner=process_owner())
        self.assertEqual(result['state'], 'cancel_requested')
        self.assertFalse(self.store.audio_path(self.job['generation_id']).exists())
        self.assertIsNone(self.client.synthesis_status('attempt')['active'])
        self.assertFalse(self.client.cancel('attempt')['remote_cancel_supported'])

    def test_already_cancelled_job_never_posts(self):
        self.store.cancel(self.job['generation_id'])
        result = self.store.execute(self.job['generation_id'], self.client, owner=process_owner())
        self.assertEqual(result['state'], 'cancelled')
        self.transport.open.assert_not_called()

    def test_redirect_does_not_forward_authorization(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.test'))
        self.transport.open.side_effect = urllib.error.HTTPError(API, 302, 'redirect', {}, io.BytesIO())
        result = self.store.execute(self.job['generation_id'], self.client, owner=process_owner())
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(self.transport.open.call_count, 1)

    def test_balance_is_raw_api_credit_without_account_identifiers(self):
        raw = json.dumps({'credit': '12.34', 'user_id': 'private-owner'}).encode()
        self.transport.open.side_effect = lambda *a, **kw: Response(raw)
        self.assertEqual(self.client.balance(), {'provider': 'fish', 'api_credit_raw': '12.34'})
        self.assertEqual(self.transport.open.call_args.args[0].full_url, API + '/wallet/self/api-credit')

    def test_voice_search_is_read_only_and_uses_documented_query(self):
        raw = json.dumps({'items': [{'_id': 'v', 'title': 'Voice', 'private_author': 'omit'}],
                          'has_more': True}).encode()
        self.transport.open.side_effect = lambda *a, **kw: Response(raw)
        result = self.client.voices(own=False, language='ru', page=2)
        self.assertEqual(result['voices'], [{'_id': 'v', 'title': 'Voice'}])
        request = self.transport.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertIn('language=ru', request.full_url)
        self.assertIn('self=false', request.full_url)

    def test_interrupted_account_response_has_a_sanitized_error(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = http.client.IncompleteRead(b'private-account-data')
        self.transport.open.side_effect = None
        self.transport.open.return_value = response
        with self.assertRaisesRegex(FishError, '^worker_disconnected$'):
            self.client.balance()

    def test_unparseable_account_response_does_not_expose_body(self):
        self.transport.open.side_effect = lambda *a, **kw: Response(b'private-account-data')
        with self.assertRaisesRegex(FishError, '^fish_invalid_response$'):
            self.client.balance()

    def test_missing_and_malformed_key_do_not_leave_audio(self):
        for key in ('', 'Bearer invalid key', '\u0440\u0443\u0441'):
            self.key.write_text(key, encoding='utf-8')
            with self.assertRaises(FishError):
                self.client.stream_intent(self.job['request'], self.base / 'missing.ogg')
            self.assertFalse((self.base / 'missing.ogg').exists())
        self.transport.open.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'Unix file permissions')
    def test_world_readable_key_is_rejected(self):
        self.key.chmod(0o644)
        with self.assertRaisesRegex(FishError, 'fish_insecure_key_file'):
            read_key(self.key)

    @unittest.skipUnless(os.name == 'posix', 'symlink fixture requires Unix')
    def test_symlink_key_is_rejected(self):
        link = self.base / 'link'
        link.symlink_to(self.key)
        with self.assertRaisesRegex(FishError, 'fish_invalid_key_file'):
            read_key(link)


if __name__ == '__main__':
    unittest.main()
