"""Adversarial regressions for reviewer agent-2cb1a4f1's reproduced routes."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hands
import workshop


class TestScopedSearch(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / 'home'
        self.repo = self.tmp / 'repo'
        self.outside = self.tmp / 'outside'
        for d in (self.home / 'inside', self.repo, self.outside):
            d.mkdir(parents=True)
        (self.home / 'inside/local.txt').write_text('needle INSIDE\n')
        (self.home / 'inside/local.py').write_text('def INSIDE_SYMBOL(): pass\n')
        (self.outside / 'escape.txt').write_text('needle OUTSIDE_SENTINEL\n')
        (self.outside / 'escape.py').write_text('def OUTSIDE_SYMBOL(): pass\n')
        (self.home / 'linkroot').symlink_to(self.outside, target_is_directory=True)
        (self.home / 'inside/external.txt').symlink_to(self.outside / 'escape.txt')
        (self.home / 'external.py').symlink_to(self.outside / 'escape.py')
        (self.home / 'goodroot').symlink_to(self.home / 'inside', target_is_directory=True)
        (self.home / 'inside/good.txt').symlink_to(self.home / 'inside/local.txt')
        (self.home / 'good.py').symlink_to(self.home / 'inside/local.py')
        (self.home / 'inside/broken.txt').symlink_to(self.tmp / 'missing')
        (self.home / 'inside/cycle.txt').symlink_to('cycle.txt')
        for name, value in [('BASE', self.home), ('REPO', self.repo)]:
            patch = mock.patch.object(workshop, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_all_reproduced_search_routes(self):
        for pattern in ('needle', 'need.e'):
            for root, glob in [('.', '../outside/*.txt'), ('linkroot', '*.txt'),
                               ('../outside', '*.txt'), ('inside', 'external.txt'),
                               ('.', '**/*.txt'), ('', '**/*.txt'),
                               ('.', 'linkroot/*.txt')]:
                with self.subTest(pattern=pattern, root=root, glob=glob):
                    out = workshop.fs_search(pattern, glob=glob, root=root)
                    self.assertNotIn('OUTSIDE_', out)
            self.assertIn('INSIDE', workshop.fs_search(pattern, glob='*.txt', root='inside'))
        self.assertFalse(hands.search('needle', glob='../outside/*.txt', root='.', base=self.home)['ok'])
        self.assertFalse(hands.search('needle', glob='*.txt', root='linkroot', base=self.home)['ok'])
        self.assertFalse(hands.search('needle', root='../outside', base=self.home)['ok'])

    def test_all_reproduced_outline_routes(self):
        for path in ('external.py', 'linkroot/escape.py', '../outside/escape.py'):
            with self.subTest(path=path):
                self.assertNotIn('OUTSIDE_SYMBOL', workshop.code_outline(path))
                self.assertFalse(hands.outline(path, base=self.home)['ok'])

    def test_valid_links_and_normal_searches(self):
        for pattern in ('needle', 'need.e'):
            for root in ('inside', 'goodroot'):
                out = workshop.fs_search(pattern, glob='*.txt', root=root)
                self.assertIn('local.txt:1: needle INSIDE', out)
                self.assertIn('good.txt:1: needle INSIDE', out)
            out = workshop.fs_search(pattern, glob='inside/*.txt', root='.')
            self.assertIn('inside/good.txt:1:', out)
            self.assertNotIn('OUTSIDE_', out)
            for glob in ('goodroot/*.txt', '**/*.txt'):
                out = workshop.fs_search(pattern, glob=glob, root='.')
                self.assertNotIn('goodroot/local.txt:1:', out)
                self.assertNotIn('goodroot/good.txt:1:', out)
        self.assertTrue(any(hit.startswith('inside/local.txt:') for hit in
                            hands.search('needle', glob='inside/*.txt', base=self.home)['hits']))
        for path in ('good.py', 'goodroot/local.py', 'inside/local.py'):
            self.assertIn('INSIDE_SYMBOL', workshop.code_outline(path))
            self.assertTrue(hands.outline(path, base=self.home)['ok'])

    def test_selected_root_is_narrower_than_home(self):
        (self.home / 'sibling.txt').write_text('needle SIBLING\n')
        (self.home / 'inside/sibling.txt').symlink_to(self.home / 'sibling.txt')
        for pattern in ('needle', 'need.e'):
            self.assertNotIn('SIBLING', workshop.fs_search(pattern, glob='*.txt', root='inside'))
            self.assertIn('inside/sibling.txt:1: needle SIBLING',
                          workshop.fs_search(pattern, glob='**/*.txt', root=''))

    def test_repo_resolution_is_shared_by_all_public_routes(self):
        (self.repo / 'r.txt').write_text('needle REPO_CONTROL\n')
        (self.repo / 'r.py').write_text('def REPO_SYMBOL(): pass\n')
        (self.home / 'repo_link').symlink_to(self.repo, target_is_directory=True)
        (self.home / 'repo.py').symlink_to(self.repo / 'r.py')
        for pattern in ('needle', 'need.e'):
            for root in (str(self.repo), 'repo_link'):
                self.assertIn('REPO_CONTROL', workshop.fs_search(pattern, glob='*.txt', root=root))
            self.assertNotIn('REPO_CONTROL', workshop.fs_search(pattern, glob='**/*.txt'))
        self.assertIn('REPO_SYMBOL', workshop.code_outline('repo.py'))
        self.assertIn('REPO_SYMBOL', workshop.code_outline('repo_link/r.py'))
        self.assertFalse(hands.outline('repo.py', base=self.home)['ok'])

    def test_bad_masks_refused_without_content_fallback(self):
        for mask in ('../outside/*.txt', 'inside/../../outside/*.txt', '/tmp/*', '..\\outside\\*'):
            for pattern in ('needle', 'need.e'):
                with self.subTest(mask=mask, pattern=pattern):
                    self.assertIn('плохая маска', workshop.fs_search(pattern, glob=mask, root='.'))

    def test_partial_home_results_survive_silent_next_child(self):
        with mock.patch.object(hands, 'search', side_effect=[
                {'ok': True, 'files_seen': 1, 'hits': ['local.txt:1: needle INSIDE']}, None]), \
             mock.patch.object(workshop, '_search_roots', return_value=['inside', 'later']), \
             mock.patch.object(Path, 'read_text', side_effect=AssertionError('parent fallback')):
            out = workshop.fs_search('needle', glob='**/*.txt')
        self.assertIn('inside/local.txt:1: needle INSIDE', out)
        self.assertIn('не ответил на later', out)
        self.assertIn('не просмотрены', out)

    def test_explicit_scope_all_masks_and_home_budget_partition(self):
        (self.home / 'sibling.txt').write_text('needle SIBLING\n')
        (self.home / 'inside/sub').mkdir()
        for rel in ('link.txt', 'sub/link.txt'):
            (self.home / 'inside' / rel).symlink_to(self.home / 'sibling.txt')
        for mask, paths in [('*.txt', ['link.txt']), ('./*.txt', ['link.txt']),
                            ('**/*.txt', ['link.txt', 'sub/link.txt']),
                            ('sub/*.txt', ['sub/link.txt']), ('**/sub/*.txt', ['sub/link.txt'])]:
            with self.subTest(mask=mask):
                result = hands.search('needle', glob=mask, root='inside', base=self.home, scope_root=self.home)
                self.assertTrue(result['ok'])
                for path in paths:
                    self.assertIn(f'{path}:1: needle SIBLING', result['hits'])
                self.assertFalse(any('OUTSIDE_' in h for h in result['hits']))
                narrow = hands.search('needle', glob=mask, root='inside', base=self.home)
                self.assertFalse(any('SIBLING' in h for h in narrow['hits']))
                for pattern in ('needle', 'need.e'):
                    self.assertNotIn('SIBLING', workshop.fs_search(pattern, glob=mask, root='inside'))
        # Exercise composite delegation with BASE authority but a partition root.
        with mock.patch.object(workshop, '_search_roots', return_value=['inside']):
            out = workshop._floor_search_home('needle', '**/sub/*.txt')
        self.assertIn('inside/sub/link.txt:1: needle SIBLING', out)

    def test_explicit_scope_cannot_widen_authorized_home(self):
        (self.home / 'scope_link').symlink_to(self.tmp, target_is_directory=True)
        for scope in (self.tmp, self.home / 'scope_link', self.home / 'missing', self.home / 'inside/local.txt'):
            for mask in ('*.txt', './*.txt', '**/*.txt', 'sub/*.txt', '**/sub/*.txt'):
                with self.subTest(scope=scope, mask=mask):
                    with mock.patch.object(hands, '_ask_search_process', side_effect=AssertionError('unauthorized dispatch')):
                        result = hands.search('needle', glob=mask, root='inside', base=self.home, scope_root=scope)
                    self.assertFalse(result['ok'])
        # An in-home scope that does not contain the traversal is invalid too.
        self.assertFalse(hands.search('needle', root='.', base=self.home, scope_root=self.home / 'inside')['ok'])

    def test_exclusions_relative_to_selected_traversal(self):
        for ancestor in ('.git', 'node_modules'):
            home = self.tmp / ancestor / 'home'
            part = home / 'part'
            (part / 'sub').mkdir(parents=True)
            for rel in ('hit.txt', 'sub/hit.txt', '.git/hidden.txt', 'node_modules/hidden.txt'):
                path = part / rel
                path.parent.mkdir(exist_ok=True)
                path.write_text('needle ANCESTOR_CONTROL\n')
            # A skip-dir name used as a file is not a directory exclusion.
            (part / 'sub/.git').write_text('needle FILENAME_CONTROL\n')
            with mock.patch.object(workshop, 'BASE', home):
                for mask in ('*.txt', './*.txt', '**/*.txt', 'sub/*.txt', '**/sub/*.txt'):
                    for pattern in ('needle', 'need.e'):
                        with self.subTest(ancestor=ancestor, mask=mask, pattern=pattern):
                            out = workshop.fs_search(pattern, glob=mask, root='part')
                            self.assertIn('ANCESTOR_CONTROL', out)
                            self.assertNotIn('hidden.txt:1:', out)
                    direct = hands.search('needle', glob=mask, root='part', base=home, scope_root=home)
                    self.assertTrue(direct['hits'])
                    self.assertFalse(any('hidden.txt:' in h for h in direct['hits']))
                for pattern in ('needle', 'need.e'):
                    self.assertIn('ANCESTOR_CONTROL', workshop.fs_search(pattern, glob='**/*.txt'))
                    # Explicit selection of an excluded-name directory is deliberate.
                    self.assertIn('hidden.txt:1:', workshop.fs_search(pattern, glob='./*.txt', root='part/.git'))
                    self.assertIn('FILENAME_CONTROL', workshop.fs_search(pattern, glob='sub/.git', root='part'))
