"""Diagnostic fields and bounded backup evidence never disclose credentials."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

if sys.platform.startswith('linux'):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
    import doctor


@unittest.skipUnless(sys.platform.startswith('linux'), 'server diagnostics are Linux only')
class ServerDoctor(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = {'agent_mode': 'interactive', 'sandbox': {'enabled': False}, 'service': {'session0': True},
            'model': {'key': 'private-model-key', 'base_url': 'https://user:password@example.test/v1?key=secret',
                      'model': 'a-model', 'framework': 'openai'}, 'telegram': {'bot_token': 'private-bot-token'}}
        self.path = self.root / 'helene.json'; self.path.write_text(json.dumps(self.cfg))

    def test_route_is_origin_only_and_credentials_are_absent(self):
        out = doctor.config_receipt(self.path)
        raw = json.dumps(out)
        for forbidden in ['private-model-key', 'private-bot-token', 'password', 'secret', 'user:']:
            self.assertNotIn(forbidden, raw)
        self.assertEqual(out['configured_base_origin'], 'https://example.test')
        self.assertTrue(out['session0']); self.assertFalse(out['sandbox_enabled'])

    def test_invalid_provider_route_and_unmapped_backup_uid_still_produce_diagnostics(self):
        self.cfg['model']['base_url'] = 'https://example.test:private-model-key'
        self.path.write_text(json.dumps(self.cfg))
        out = doctor.config_receipt(self.path)
        self.assertEqual(out['configured_base_origin'], '[invalid route]')
        folder = self.root / '.updater/backups/foreign-uid'; folder.mkdir(parents=True)
        with patch.object(doctor.pwd, 'getpwuid', side_effect=KeyError('unknown')):
            self.assertEqual(doctor.backup_receipts(self.root)[0]['owner'], str(folder.stat().st_uid))

    def test_backup_inventory_has_actual_sizes_and_never_follows_symlinks(self):
        folder = self.root / '.updater/backups/actual-date'; folder.mkdir(parents=True)
        (folder / 'bytes').write_bytes(b'1234567')
        (folder / 'foreign').symlink_to(self.path)
        out = doctor.backup_receipts(self.root)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['bytes_at_least'], 7); self.assertTrue(out[0]['complete'])
        self.assertEqual(out[0]['path'], str(folder))
        self.assertFalse(doctor.bounded_size(folder, max_files=0)['complete'])

    def test_container_inspect_env_is_not_reported_and_actual_config_match_is_compared(self):
        host = doctor.config_receipt(self.path)
        box = {'Id': 'container', 'Image': 'image', 'State': {'Running': True, 'Pid': 99},
               'Config': {'Env': ['TOKEN=private-value']}, 'Mounts': []}
        replies = [SimpleNamespace(returncode=0, stdout=json.dumps([box])),
                   SimpleNamespace(returncode=0, stdout=json.dumps({'config_sha256': host['sha256']}))]
        before = self.path.read_bytes()
        with patch.object(doctor.subprocess, 'run', side_effect=replies) as run:
            out = doctor.collect(self.root, 'helene')
        self.assertTrue(out['config_bytes_match'])
        self.assertNotIn('private-value', json.dumps(out))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(run.call_args_list[0].args[0], ['docker', 'inspect', 'helene'])
        self.assertEqual(run.call_args_list[1].args[0][:3], ['docker', 'exec', 'helene'])

    def test_backup_root_symlinks_are_not_traversed(self):
        foreign = self.root / 'foreign'; (foreign / 'backups/private-copy').mkdir(parents=True)
        updater = self.root / '.updater'; updater.symlink_to(foreign, target_is_directory=True)
        self.assertEqual(doctor.backup_receipts(self.root), [])
        updater.unlink(); updater.mkdir()
        (updater / 'backups').symlink_to(foreign / 'backups', target_is_directory=True)
        self.assertEqual(doctor.backup_receipts(self.root), [])

    def test_actual_container_probe_does_not_disclose_malformed_config_fields(self):
        secret = 'private-bot-token'
        self.cfg.update(agent_mode={'token': secret}, sandbox={'enabled': secret},
                        service={'session0': {'key': secret}})
        self.path.write_text(json.dumps(self.cfg))
        (self.root / 'tree').mkdir()
        (self.root / 'tree/work_loop.py').write_text('def owner_followup(): pass\n')
        (self.root / 'helene-build.json').write_text(json.dumps({'version': {'key': secret}}))
        real_run = doctor.subprocess.run
        box = {'Id': 'container', 'Image': 'image', 'State': {'Running': True, 'Pid': 99}, 'Mounts': []}
        def fake_docker(args, **kwargs):
            if args[1] == 'inspect':
                return SimpleNamespace(returncode=0, stdout=json.dumps([box]))
            code = args[-1].replace('/opt/helene/helene.json', str(self.path))
            return real_run([sys.executable, '-c', code], **kwargs)
        before = self.path.read_bytes()
        with patch.object(doctor.subprocess, 'run', side_effect=fake_docker):
            out = doctor.collect(self.root, 'helene')
        self.assertTrue(out['config_bytes_match'])
        self.assertNotIn(secret, json.dumps(out))
        self.assertIsNone(out['container_source']['agent_mode'])
        self.assertIsNone(out['container_source']['sandbox_enabled'])
        self.assertIsNone(out['container_source']['session0'])
        self.assertIsNone(out['container_source']['version'])
        self.assertTrue(out['container_source']['owner_followup_present'])
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
