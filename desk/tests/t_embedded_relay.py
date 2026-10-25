"""Embedded adaptation selection goes through the real stage/verify boundaries."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / 'installer'))
import embedded_relay
import vnext_dist


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL).decode().strip()


class EmbeddedSelection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.repo, self.source, self.stage = base/'repo', base/'source', base/'stage'
        self.repo.mkdir(); self.source.mkdir()
        files = {'Cargo.toml': b'[package]\nname="relay"\nversion="0.1.0"\n',
                 'Cargo.lock': b'version = 3\n', 'LICENSE': b'MIT\n', 'NOTICE': b'attribution\n',
                 'src/main.rs': b'fn main() {}\n'}
        for name, data in files.items():
            path = self.source/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        descriptor = {'product':'embedded-helene-relay', 'source_commit':'f'*40,
                      'source_files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}
        target = self.repo/embedded_relay.DESCRIPTOR
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(descriptor), encoding='utf-8')
        (self.repo/embedded_relay.RECIPE).write_bytes(b'FROM rust:1-bookworm\n')
        git(self.repo,'init','-q');git(self.repo,'-c','core.autocrlf=false','add','-A')
        git(self.repo,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','pin')
        self.head = git(self.repo,'rev-parse','HEAD')

    def test_selected_embedded_source_survives_original_removal_but_not_staged_drift(self):
        note = embedded_relay.stage(self.repo,self.head,self.source,self.stage)
        self.assertEqual(note['source_commit'],'f'*40)
        self.assertEqual(vnext_dist.verify_relay(self.repo,self.head,self.stage,kind='embedded'), note)
        # The independent universal CLI must not certify these same bytes.
        with self.assertRaises(Exception):
            vnext_dist.verify_relay(self.repo,self.head,self.stage)
        (self.source/'src/main.rs').unlink()
        self.assertEqual(embedded_relay.verify(self.repo,self.head,self.stage),note)
        (self.stage/'src/main.rs').write_bytes(b'foreign')
        with self.assertRaisesRegex(ValueError,'inventory differs'):
            embedded_relay.verify(self.repo,self.head,self.stage)

    def test_extra_or_modified_input_is_refused_without_destination(self):
        (self.source/'extra.txt').write_bytes(b'not pinned')
        with self.assertRaisesRegex(ValueError,'exact hotfix source map'):
            embedded_relay.stage(self.repo,self.head,self.source,self.stage)
        self.assertFalse(self.stage.exists())

    def test_recipe_change_does_not_reuse_old_build_identity(self):
        embedded_relay.stage(self.repo,self.head,self.source,self.stage)
        (self.repo/embedded_relay.RECIPE).write_bytes(b'FROM rust:other\n')
        with self.assertRaisesRegex(ValueError,'source moved'):
            embedded_relay.verify(self.repo,self.head,self.stage)


if __name__ == '__main__':
    unittest.main()
