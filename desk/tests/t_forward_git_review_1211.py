"""A forward archive must outlive the source machine and every Git common dir."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / 'localharness')]
from localharness import carry

GIT = shutil.which('git')


@unittest.skipUnless(GIT, 'Git is required for the actual portable history check')
class ForwardGit(unittest.TestCase):
    def test_worktrees_keep_history_index_and_dirty_files_without_original_repo(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            here, there, common = root / 'here', root / 'there', root / 'common'
            for program in (here, there):
                (program / 'data/workspace').mkdir(parents=True)
                (program / 'tree').mkdir()
                (program / 'helene.json').write_text(json.dumps({'tree': 'data', 'code': 'tree'}))
            common.mkdir()
            env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
            env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1', GIT_AUTHOR_NAME='Fixture',
                       GIT_AUTHOR_EMAIL='fixture@example.invalid', GIT_COMMITTER_NAME='Fixture',
                       GIT_COMMITTER_EMAIL='fixture@example.invalid')

            def git(repo, *args):
                return subprocess.check_output([GIT, '-C', str(repo), *args], env=env,
                                               stderr=subprocess.STDOUT, text=True).strip()

            git(common, 'init', '-q', '-b', 'main')
            (common / 'hello.txt').write_text('committed\n')
            git(common, 'add', '.')
            git(common, 'commit', '-qm', 'history on the old computer')
            project = here / 'data/workspace/project'
            external = root / 'external'
            for name, path in (('project', project), ('external', external), ('code', here / 'tree')):
                if path.exists():
                    path.rmdir()  # empty fixture folder only
                git(common, 'worktree', 'add', '-qb', name, str(path))
                (path / 'hello.txt').write_text('staged\n')
                git(path, 'add', 'hello.txt')
                (path / 'hello.txt').write_text('dirty\n')
                (path / 'untracked.txt').write_text('untracked\n')
            archive = Path(carry.export(here / 'helene.json', root / 'carry.zip', projects=[external])['archive'])
            common.rename(root / 'common-retained')  # every old .git pointer now breaks
            receipt = carry.import_(there / 'helene.json', archive)
            for repo in (there / 'data/workspace/project', there / 'data/workspace/projects/external', there / 'tree'):
                self.assertTrue((repo / '.git').is_dir(), str(repo))
                self.assertEqual(git(repo, 'log', '-1', '--format=%s'), 'history on the old computer')
                self.assertEqual(git(repo, 'show', ':hello.txt'), 'staged')
                if repo != there / 'tree':  # code applies through its separate code-carry policy
                    self.assertEqual((repo / 'hello.txt').read_text(), 'dirty\n')
                    self.assertEqual((repo / 'untracked.txt').read_text(), 'untracked\n')
                git(repo, 'fsck', '--connectivity-only')
            self.assertGreater(receipt['code_git']['files'], 0)


class UnreadableTree(unittest.TestCase):
    def test_scan_error_is_not_an_empty_successful_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)

            def denied(top, *args, **kwargs):
                error = PermissionError(13, 'denied', str(root / 'memory'))
                if kwargs.get('onerror'):
                    kwargs['onerror'](error)
                return iter(())

            with mock.patch.object(carry.os, 'walk', denied):
                with self.assertRaisesRegex((OSError, ValueError), 'memory'):
                    carry._scan(root)


if __name__ == '__main__':
    unittest.main()
