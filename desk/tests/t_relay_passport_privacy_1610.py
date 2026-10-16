"""A frozen relay's local origin is provenance, not a public package field."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK / 'installer'))
import build_dist


class RelayPassportPrivacy(unittest.TestCase):
    def test_preserves_digest_and_receipt_without_private_location(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / 'relay.exe'
            exe.write_bytes(b'fixture executable')
            digest = hashlib.sha256(b'fixture source').hexdigest()
            original = {'from': 'private-origin-marker:/private/tree', 'note': 'private operational detail',
                        'commit': 'private-origin-marker:/private/tree',
                        'public_commit': 'private operational detail', 'dirty': False,
                        'pulled_utc': 'private operational detail', 'digest': digest, 'files': 1}
            built = {'digest': digest, 'exe_sha256': hashlib.sha256(exe.read_bytes()).hexdigest(),
                     'exe': 'private-origin-marker:/private/tree',
                     'built_utc': 'private operational detail', 'note': 'private operational detail'}
            shown = io.StringIO()
            with mock.patch.object(build_dist.relay_src, 'digest', return_value=(digest, ['src/lib.rs'])), \
                 mock.patch.object(build_dist.relay_src, 'stamp', return_value=original), \
                 mock.patch.object(build_dist.relay_src, 'built', return_value=built), \
                 contextlib.redirect_stdout(shown):
                row = build_dist.relay_provenance(exe, allow_partial=False)
            self.assertEqual(row['digest'], digest)
            self.assertEqual(row['source'], {'digest': digest, 'files': 1})
            self.assertEqual(row['built'], {k: built[k] for k in ('digest', 'exe_sha256')})
            self.assertNotIn('from', row['source'])
            self.assertNotIn('note', row['source'])
            self.assertNotIn('private-origin-marker', json.dumps(row) + shown.getvalue())
            self.assertNotIn('private operational detail', json.dumps(row) + shown.getvalue())
            self.assertEqual(original['from'], 'private-origin-marker:/private/tree',
                             'the private source stamp itself must stay byte-identical')

    def test_linux_receipt_keeps_proof_not_extra_private_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / 'helene-relay'
            binary.write_bytes(b'linux fixture executable')
            digest = hashlib.sha256(b'linux fixture source').hexdigest()
            built = {'digest': digest, 'target': 'private-origin-marker:/private/tree',
                     'exe': 'private operational detail',
                     'exe_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                     'built_utc': 'private operational detail', 'from': 'private-origin-marker:/private/tree'}
            with mock.patch.object(build_dist.relay_src, 'digest', return_value=(digest, ['src/lib.rs'])), \
                 mock.patch.object(build_dist.relay_src, 'built_linux', return_value=built), \
                 contextlib.redirect_stdout(io.StringIO()):
                row = build_dist.relay_linux_provenance(binary, allow_partial=False)
                self.assertEqual(row, {k: built[k] for k in ('digest', 'exe_sha256')})
                self.assertNotIn('from', row)
                row = build_dist.relay_linux_provenance(Path(folder) / 'missing', allow_partial=True)
                self.assertTrue(row['stale'])
                self.assertNotIn('from', row)


if __name__ == '__main__':
    unittest.main(verbosity=2)
