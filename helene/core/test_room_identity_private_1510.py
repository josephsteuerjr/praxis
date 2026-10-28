"""The published tree never chooses a Telegram root group by guesswork."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frame_serve
import keat_readiness
import moderation_shadow
import room_identity
import telegram_admin
import telegram_moderation

SYNTHETIC = '-100999999999999999'  # deliberately outside Telegram's room-ID range


class ExplicitRootIdentity(unittest.TestCase):
    def test_exact_binding_only(self):
        for candidate in (None, '', '  ' + SYNTHETIC, SYNTHETIC + ' ',
                          SYNTHETIC.lstrip('-'), '+100999999999999999',
                          '-100000000000000000', '*', '0'):
            with self.subTest(candidate_type=type(candidate).__name__), patch.dict(
                    os.environ, {} if candidate is None else {
                        room_identity.ROOT_ROOM_ENV: candidate}, clear=True):
                self.assertIsNone(room_identity.root_chat_id())
                self.assertIsNone(room_identity.root_peer_id())
        self.assertEqual(room_identity.root_chat_id({room_identity.ROOT_ROOM_ENV: SYNTHETIC}), SYNTHETIC)
        self.assertEqual(room_identity.root_peer_id({room_identity.ROOT_ROOM_ENV: SYNTHETIC}), int(SYNTHETIC))

    def test_no_fallback_from_legacy_rollout_or_other_groups(self):
        ctx = SimpleNamespace(chat_id=SYNTHETIC, room_chat_id=SYNTHETIC,
                              is_dm=False, telegram_root_group=True)
        for candidate in (None, 'bad', '-100999999999999998'):
            env = {'PRAXIS_KEAT': 'serve',
                   'PRAXIS_KEAT_PAIRS': '[["group","' + SYNTHETIC + '"]]'
                   } if candidate is None else {room_identity.ROOT_ROOM_ENV: candidate}
            with patch.dict(os.environ, env, clear=True):
                self.assertFalse(frame_serve._root_group(ctx))
                self.assertFalse(keat_readiness._serving_adapter_ready(dict(
                    mode='group', stream=SYNTHETIC,
                    capture=dict(audience=['group:' + SYNTHETIC], transfer='none',
                                 presence_hidden=False))))
                self.assertEqual(moderation_shadow.detect_message(
                    peer_id=int(SYNTHETIC), text='куплю пиши', first_message=True).verdict, 'pass')
                with self.assertRaises(PermissionError):
                    telegram_moderation.validate(int(SYNTHETIC), 1, 2, 'delete')
                with self.assertRaises(PermissionError):
                    telegram_admin.normalize(int(SYNTHETIC), 'slow_mode', {'seconds': 10})
        with patch.dict(os.environ, {room_identity.ROOT_ROOM_ENV: SYNTHETIC}, clear=True):
            self.assertTrue(frame_serve._root_group(ctx))
            self.assertEqual(moderation_shadow.detect_message(
                peer_id=int(SYNTHETIC), text='куплю пиши', first_message=True).verdict, 'review')
            self.assertIsInstance(telegram_moderation.validate(
                int(SYNTHETIC), 1, 2, 'delete'), str)
            self.assertEqual(telegram_admin.normalize(
                int(SYNTHETIC), 'slow_mode', {'seconds': 10}), ('slow_mode', {'seconds': 10}))
