import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from memory_history import HistoryError, snapshot


class MemoryHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.memory = self.source / 'memory'
        (self.memory / 'people').mkdir(parents=True)
        self.text = self.memory / 'people/alice.md'
        self.text.write_text('# Alice\nFirst version\n')
        self.manifest = self.root / 'manifest.json'
        self.manifest.write_text(json.dumps(['people/alice.md']))
        self.history = self.root / 'private-history.git'

    def snap(self, initialize=False):
        return snapshot(self.memory, self.history, self.manifest, self.source,
                        initialize=initialize)

    def git(self, *args):
        return subprocess.check_output(['git', '--git-dir=' + str(self.history), *args]).decode(errors='replace')

    def test_versions_deletion_and_no_remote(self):
        first = self.snap(True)
        self.assertEqual(first, self.snap())
        self.text.write_text('# Alice\nSecond version\n')
        second = self.snap()
        self.assertNotEqual(first, second)
        self.assertIn('First version', self.git('show', first + ':people/alice.md'))
        self.assertIn('Second version', self.git('show', second + ':people/alice.md'))
        self.text.unlink()
        self.snap()
        self.assertEqual('', self.git('ls-tree', '-r', '--name-only', 'refs/heads/memory'))
        self.assertEqual('', self.git('remote'))
        self.assertFalse((self.memory / '.git').exists())
        self.assertFalse((self.source / '.git').exists())

    def test_unlisted_runtime_never_read_or_stored(self):
        for name in ['.state/credentials.md', 'mailbox.json', 'sessions/x.session',
                     'runs/log.md', 'notes/events.jsonl', 'people/unapproved.md']:
            path = self.memory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('api_key=SECRET_RUNTIME_VALUE')
        self.snap(True)
        self.assertEqual('people/alice.md\n', self.git('ls-tree', '-r', '--name-only', 'refs/heads/memory'))
        dump = self.git('cat-file', '--batch-all-objects', '--batch')
        self.assertNotIn('SECRET_RUNTIME_VALUE', dump)

    def test_allowlist_rejects_runtime_and_traversal(self):
        for name in ['../escape.md', '/tmp/a.md', '.state/key.md', 'runs/log.md',
                     'notes/events.jsonl', 'people/x.session.md', 'notes/password.md',
                     'rooms/../alice.md', 'INDEX.md', 'people/_пример.md', 'people/sub/x.md']:
            with self.subTest(name=name):
                self.manifest.write_text(json.dumps([name]))
                with self.assertRaises(HistoryError):
                    self.snap(True)
                self.assertFalse(self.history.exists())

    def test_secret_refuses_before_writing_objects(self):
        first = self.snap(True)
        before = set(self.git('cat-file', '--batch-all-objects', '--batch-check').splitlines())
        for text in ['api_key="example_private_value"', 'Bearer abcdefghijklmnopqrst',
                     '-----BEGIN OPENSSH PRIVATE KEY-----', 'https://user:password@example.test',
                     '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk']:
            self.text.write_text(text)
            with self.assertRaises(HistoryError):
                self.snap()
        self.assertEqual(first, self.git('rev-parse', 'refs/heads/memory').strip())
        self.assertEqual(before, set(self.git('cat-file', '--batch-all-objects', '--batch-check').splitlines()))

    def test_symlink_and_hardlink_refused(self):
        self.text.unlink()
        outside = self.root / 'outside.md'
        outside.write_text('private')
        self.text.symlink_to(outside)
        with self.assertRaises(HistoryError):
            self.snap(True)
        self.text.unlink()
        os.link(outside, self.text)
        with self.assertRaises(HistoryError):
            self.snap(True)

    def test_opt_in_location_remote_and_lock_guards(self):
        with self.assertRaises(HistoryError):
            self.snap()
        for location in [self.source / 'history', self.memory / 'history', self.root]:
            with self.assertRaises(HistoryError):
                snapshot(self.memory, location, self.manifest, self.source, initialize=True)
        self.snap(True)
        subprocess.check_call(['git', '--git-dir=' + str(self.history), 'config',
                               'remote.bad.url', '/nonexistent/local-only'])
        with self.assertRaises(HistoryError):
            self.snap()
        subprocess.check_call(['git', '--git-dir=' + str(self.history), 'config',
                               '--remove-section', 'remote.bad'])
        lock = self.root / '.private-history.git.lock'
        lock.touch()
        with self.assertRaises(FileExistsError):
            self.snap()
        self.assertTrue(lock.exists())

    def test_rooms_and_authored_markdown_notes_are_exact_bytes(self):
        entries = ['rooms/example.md', 'notes/authored.md', 'home.md']
        payload = '# Авторский текст\nСтрока с UTF-8.\n'.encode()
        for name in entries:
            path = self.memory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        self.manifest.write_text(json.dumps(entries))
        commit = self.snap(True)
        for name in entries:
            actual = subprocess.check_output(['git', '--git-dir=' + str(self.history),
                                              'show', commit + ':' + name])
            self.assertEqual(payload, actual)

    def test_nested_repository_symlink_refused_before_object_writes(self):
        import hashlib
        self.snap(True)
        data = b'# A changed approved text\n'
        digest = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        outside = self.root / 'external-objects'
        outside.mkdir()
        prefix = self.history / 'objects' / digest[:2]
        if prefix.exists():
            for existing in prefix.iterdir():
                existing.rename(outside / existing.name)
            prefix.rmdir()
        prefix.symlink_to(outside, target_is_directory=True)
        before = sorted(p.name for p in outside.iterdir())
        self.text.write_bytes(data)
        with self.assertRaises(HistoryError):
            self.snap()
        self.assertEqual(before, sorted(p.name for p in outside.iterdir()))

    def test_history_inside_bare_repository_refused_before_creation(self):
        outer = self.root / 'public.git'
        subprocess.run(['git', 'init', '--bare', str(outer)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.history = outer / 'private-history'
        with self.assertRaises(HistoryError):
            self.snap(True)
        self.assertFalse(self.history.exists())
        self.assertFalse((outer / '.private-history.lock').exists())

    def test_git_environment_cannot_redirect_objects(self):
        from unittest.mock import patch
        redirected = self.root / 'redirected'
        with patch.dict(os.environ, {'GIT_OBJECT_DIRECTORY': str(redirected),
                                    'GIT_DIR': str(redirected)}):
            self.snap(True)
        self.assertFalse(redirected.exists())


if __name__ == '__main__':
    unittest.main()
