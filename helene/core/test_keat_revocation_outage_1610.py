"""Synthetic native enrollment -> outage -> edit/delete -> restore regression.

No Telegram client is started. The runner handlers are called with constructed
updates; the durable ledger/policy live under a disposable local directory.
"""
import asyncio
from contextlib import nullcontext
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = '-100999999999999999'
OTHER = '-100' + '888888888888888'  # synthetic fixture, not a room identity


class WindowsSafeRevocationRouting(unittest.TestCase):
    """Exercise policy/ledger lookup without replacing the POSIX durability test."""

    def test_default_off_without_policy_does_not_break_ordinary_edits_or_deletes(self):
        import keat_live as live
        import mtproto_runner as runner
        env = dict(PRAXIS_ABSTRACTDL_ROOT_CHAT_ID=ROOT,
                   PRAXIS_KEAT_CAPTURE='off')
        with patch.dict(os.environ, env, clear=True), \
             patch.object(runner, '_group_archive_enabled', return_value=False):
            self.assertFalse(live.enrolled_native_group(ROOT))
            asyncio.run(runner.on_edited(SimpleNamespace(
                chat_id=int(ROOT), is_private=False,
                message=SimpleNamespace(id=9, edit_date=None))))
            asyncio.run(runner.on_deleted(SimpleNamespace(
                chat_id=int(ROOT), deleted_ids=[9])))
            asyncio.run(runner.on_deleted(SimpleNamespace(
                chat_id=None, deleted_ids=[9])))
        # A turned-on capture without its policy is a configuration failure,
        # not evidence that existing history is empty.
        from keat_candidate import ContractError
        with patch.dict(os.environ, dict(PRAXIS_ABSTRACTDL_ROOT_CHAT_ID=ROOT,
                                         PRAXIS_KEAT_CAPTURE='on'), clear=True):
            with self.assertRaises(ContractError):
                live.enrolled_native_group(ROOT)
            with self.assertRaises(ContractError):
                live.invalidate_peerless_deletion([9])

    def test_captured_group_identity_is_negative_authority_during_outage(self):
        import keat_live as live
        with tempfile.TemporaryDirectory() as root:
            policy_path = Path(root) / 'policy.json'
            policy_path.write_text(json.dumps(dict(
                schema='keat.capture-policy.v1', namespace='routing-canary', root=root,
                enrollments=[dict(mode='group', stream=ROOT, capture=dict(
                    issuer='offline', policy_revision='root.v1',
                    audience=['group:' + ROOT], transfer='none', presence_hidden=False))]
            )), encoding='utf-8')
            receipt = dict(kind='message', key='telegram:' + ROOT + ':message:1:in',
                           event='old', capture={'audience': ['group:' + ROOT]})
            payload = {'old': {'telegram_root_chat_id': ROOT,
                               'telegram_ordinary_root': True}}
            env = dict(PRAXIS_KEAT_CAPTURE_POLICY=str(policy_path),
                       PRAXIS_KEAT_CAPTURE='off', PRAXIS_ABSTRACTDL_ROOT_CHAT_ID='bad')
            with patch.dict(os.environ, env, clear=True), \
                 patch.object(live.CaptureLedger, 'guard', return_value=nullcontext()), \
                 patch.object(live.CaptureLedger, '_state',
                              return_value=([receipt], payload, {}, {}, None, {})), \
                 patch.object(live.CaptureLedger, 'invalidate_prefix') as invalidate:
                self.assertTrue(live.enrolled_native_group(ROOT))
                self.assertFalse(live.enrolled_native_group(OTHER))
                live.invalidate_native(ROOT, 1)
                invalidate.assert_called_once_with('telegram:' + ROOT + ':message:1:')
            # Merely editing the admin policy cannot make an unbound group a
            # historically enrolled root; immutable capture evidence is needed.
            with patch.dict(os.environ, env, clear=True), \
                 patch.object(live.CaptureLedger, 'guard', return_value=nullcontext()), \
                 patch.object(live.CaptureLedger, '_state',
                              return_value=([], {}, {}, {}, None, {})):
                self.assertFalse(live.enrolled_native_group(ROOT))


@unittest.skipIf(os.name == 'nt', 'capture ledger uses POSIX fcntl; run on Linux')
class RevocationOutage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.policy = Path(self.temp.name) / 'policy.json'
        self.env = dict(PRAXIS_ABSTRACTDL_ROOT_CHAT_ID=ROOT,
                        PRAXIS_KEAT_CAPTURE='on',
                        PRAXIS_KEAT_CAPTURE_POLICY=str(self.policy),
                        PRAXIS_KEAT_ROOT=self.temp.name,
                        PRAXIS_KEAT='serve',
                        PRAXIS_KEAT_PAIRS='[["group","' + ROOT + '"]]')
        self.policy.write_text(json.dumps(dict(
            schema='keat.capture-policy.v1', namespace='outage-canary',
            root=self.temp.name, enrollments=[dict(
                stream=ROOT, mode='group', capture=dict(
                    issuer='offline', policy_revision='root.v1',
                    audience=['group:' + ROOT], transfer='none',
                    presence_hidden=False))])), encoding='utf-8')

    @staticmethod
    def ctx(mid):
        return SimpleNamespace(chat_id=ROOT, room_chat_id=ROOT, is_dm=False,
                               owner=False, owner_audience=False, principal_id='222',
                               origin_message_id=mid, praxis_self=False,
                               telegram_root_group=True)

    @staticmethod
    def capture(live, mid):
        return live.capture_ingress(
            ROOT, is_dm=False, source_id=str(mid), direction='in',
            payload={'role': 'user', 'content': 'Bob: original', 'actor': 'Member'},
            root_chat_id=ROOT, principal_id='222', ordinary_root=True)

    def test_history_revoked_after_binding_or_capture_outage(self):
        import keat_live as live
        import mtproto_runner as runner
        for serial, (outage, operation) in enumerate((
            ('unset', 'edit'), ('malformed', 'delete'), ('capture_off', 'edit'),
            ('capture_off', 'delete'), ('unset', 'ambiguous_edit'),
        ), start=1):
            with self.subTest(outage=outage, operation=operation):
                policy = json.loads(self.policy.read_text(encoding='utf-8'))
                policy['namespace'] = 'outage-' + str(serial)
                self.policy.write_text(json.dumps(policy), encoding='utf-8')
                with patch.dict(os.environ, self.env, clear=True):
                    ref = self.capture(live, serial)
                    self.assertIsNotNone(ref)
                    sidecar = {'history': [], 'current': [ref]}
                    self.assertIsNotNone(live.adopt_projection(
                        self.ctx(serial), [], 'Bob: original', sidecar))
                missing = dict(self.env)
                if outage == 'unset':
                    missing.pop('PRAXIS_ABSTRACTDL_ROOT_CHAT_ID')
                elif outage == 'malformed':
                    missing['PRAXIS_ABSTRACTDL_ROOT_CHAT_ID'] = 'not-a-room'
                else:
                    missing['PRAXIS_KEAT_CAPTURE'] = 'off'
                with patch.dict(os.environ, missing, clear=True):
                    self.assertTrue(live.enrolled_native_group(ROOT))
                    self.assertFalse(live.enrolled_native_group(OTHER))
                    self.assertIsNone(live.adopt_projection(
                        self.ctx(serial), [], 'Bob: original', sidecar))
                    self.assertIsNone(self.capture(live, serial + 100))
                    if operation == 'delete':
                        with patch.object(runner, '_group_archive_enabled', return_value=False):
                            asyncio.run(runner.on_deleted(SimpleNamespace(
                                chat_id=int(ROOT), deleted_ids=[serial])))
                    else:
                        mid = ' ' + str(serial) + ' ' if operation == 'ambiguous_edit' else serial
                        asyncio.run(runner.on_edited(SimpleNamespace(
                            chat_id=int(ROOT), is_private=False,
                            message=SimpleNamespace(id=mid, edit_date=None))))
                with patch.dict(os.environ, self.env, clear=True):
                    self.assertIsNone(live.adopt_projection(
                        self.ctx(serial), [], 'Bob: original', sidecar))
                    self.assertIsNone(self.capture(live, serial))

    def test_policy_row_alone_cannot_revoke_unbound_group(self):
        import keat_live as live
        import mtproto_runner as runner
        missing = dict(self.env)
        missing.pop('PRAXIS_ABSTRACTDL_ROOT_CHAT_ID')
        with patch.dict(os.environ, missing, clear=True):
            self.assertFalse(live.enrolled_native_group(ROOT))
            asyncio.run(runner.on_edited(SimpleNamespace(
                chat_id=int(ROOT), is_private=False,
                message=SimpleNamespace(id=7, edit_date=None))))
        with patch.dict(os.environ, self.env, clear=True):
            self.assertIsNotNone(self.capture(live, 7))


if __name__ == '__main__':
    unittest.main()
