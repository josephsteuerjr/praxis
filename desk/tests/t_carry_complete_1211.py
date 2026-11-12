"""The transfer archive carries everything of the agent and names what stays.

12.11 (owner): «память, код, проекты — всё». The legacy archive silently dropped
links/junctions, empty folders, every *.log, mounted projects and the code's own
Git history. Same format, read by older importers; nothing is silently skipped.
"""
import json, os, shutil, subprocess, sys, tempfile, unittest, zipfile
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / 'localharness')]
from localharness import carry  # noqa: E402

GIT = shutil.which('git')


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def link(path, target):
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(path), str(target)], check=True, capture_output=True)
    else:
        path.symlink_to(target, target_is_directory=True)


def installation(root, name):
    program = root / name
    write(program / 'helene.json', json.dumps({'mode': 'local', 'tree': 'data', 'code': 'tree', 'port': 8094,
                                               'agent': {'name': 'Мира'}, 'model': {'model': 'sol'}}))
    (program / 'tree').mkdir(parents=True)
    (program / 'data').mkdir()
    return program


class Complete(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.program = installation(self.root, 'here')
        data = self.data = self.program / 'data'
        write(data / 'memory/story.md', 'memory\n')
        write(data / 'workspace/build/run.log', 'project journal\n')
        write(data / 'runner.log', 'this computer\n')
        (data / 'workspace/empty/inner').mkdir(parents=True)
        write(data / 'workspace/notes/one.md', 'note\n')
        link(data / 'workspace/latest', data / 'workspace/notes')
        write(data / 'models/whisper.bin', 'machine')
        link(data / 'workspace/voice', data / 'models')
        self.site, self.private = self.root / 'site', self.root / 'private'
        write(self.site / 'index.html', 'site\n')
        write(self.site / 'logs/access.log', 'site journal\n')
        write(self.private / 'secret.md', 'stays\n')
        link(data / 'workspace/mnt/site', self.site)
        link(data / 'workspace/mnt/private', self.private)

    def export(self, **options):
        passport = carry.export(self.program / 'helene.json', self.root / 'out.zip', **options)
        with zipfile.ZipFile(passport['archive']) as archive:
            return passport, set(archive.namelist())

    def test_everything_of_the_agent_travels_and_what_stays_is_named(self):
        passport, names = self.export(projects=[self.site])
        self.assertIn('data/workspace/build/run.log', names)
        self.assertNotIn('data/runner.log', names)
        self.assertIn('*.log в корне данных (журналы процессов этого компьютера)', passport['skipped'])
        self.assertIn('data/workspace/empty/inner/', names)
        self.assertIn('data/workspace/projects/site/index.html', names)
        self.assertIn('data/workspace/projects/site/logs/access.log', names)
        self.assertIn('data/workspace/projects/ПРОЕКТЫ-ПЕРЕНОСА.md', names)
        self.assertFalse(any('secret.md' in name for name in names))
        self.assertFalse(any(name.startswith('data/models/') for name in names))
        left = {row['path']: row['reason'] for row in passport['not_carried']}
        self.assertEqual(left['workspace/mnt/private'], 'проект остался на этом компьютере')
        self.assertEqual(left['workspace/voice'], 'ведёт в служебную папку этого компьютера')
        self.assertEqual(passport['projects'], {str(self.site): 'workspace/projects/site'})
        self.assertEqual(passport['links'], 2)

    def test_import_recreates_folders_links_and_reports_what_stayed(self):
        passport, _ = self.export(projects=[self.site])
        there = installation(self.root, 'there')
        receipt = carry.import_(there / 'helene.json', Path(passport['archive']))
        data = there / 'data'
        self.assertTrue((data / 'workspace/empty/inner').is_dir())
        self.assertEqual((data / 'workspace/latest/one.md').read_text(encoding='utf-8'), 'note\n')
        self.assertEqual((data / 'workspace/mnt/site/index.html').read_text(encoding='utf-8'), 'site\n')
        self.assertEqual((data / 'workspace/mnt/site').resolve(), (data / 'workspace/projects/site').resolve())
        self.assertEqual(receipt['links'], {'created': 2, 'failed': []})
        self.assertIn('workspace/mnt/private', [row['path'] for row in receipt['not_carried']])
        self.assertFalse((data / 'workspace/mnt/private').exists())
        # The source agent is untouched.
        self.assertEqual((self.site / 'index.html').read_text(encoding='utf-8'), 'site\n')

    def test_older_importer_still_reads_the_archive(self):
        passport, names = self.export()
        self.assertIn(carry.LINKS, names)
        with zipfile.ZipFile(passport['archive']) as archive:
            # What a pre-12.11 importer did: data/ files only, directories and side files ignored.
            files = [info for info in archive.infolist() if info.filename.startswith('data/') and not info.is_dir()]
        self.assertTrue(files)
        self.assertNotIn('data/workspace/latest/one.md', names)

    @unittest.skipUnless(GIT, 'git is required')
    def test_code_history_travels_and_never_overwrites_another_history(self):
        code = self.program / 'tree'
        write(code / 'agent.py', 'print(1)\n')
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_AUTHOR_NAME='a', GIT_AUTHOR_EMAIL='a@e',
                   GIT_COMMITTER_NAME='a', GIT_COMMITTER_EMAIL='a@e')
        run = lambda *args, cwd=code: subprocess.run(['git', *args], cwd=cwd, env=env, check=True,  # noqa: E731
                                                     capture_output=True, text=True).stdout.strip()
        run('init', '-q', '-b', 'main')
        run('add', '.')
        run('commit', '-q', '-m', 'my own code edit')
        passport, names = self.export()
        self.assertGreater(passport['code_git'], 0)
        self.assertIn('code/git/HEAD', names)
        there = installation(self.root, 'there')
        write(there / 'tree/agent.py', 'print(1)\n')
        receipt = carry.import_(there / 'helene.json', Path(passport['archive']))
        self.assertEqual(receipt['code_git']['place'], str((there / 'tree/.git').resolve()))
        self.assertEqual(run('log', '-1', '--format=%s', cwd=there / 'tree'), 'my own code edit')
        again = installation(self.root, 'third')
        (again / 'tree/.git').mkdir()
        receipt = carry.import_(again / 'helene.json', Path(passport['archive']))
        self.assertIn('workspace/code-git-', receipt['code_git']['note'])
        self.assertEqual(list((again / 'tree/.git').iterdir()), [])

    def test_transfer_review_lists_projects_and_accepts_only_reviewed_choices(self):
        try:
            from localharness import server_transfer
        except ImportError as error:  # paramiko is a runtime dependency of the transfer module
            self.skipTest(str(error))
        config = self.program / 'helene.json'
        value = json.loads(config.read_text(encoding='utf-8'))
        value['sandbox'] = {'mounts': [str(self.site)]}
        config.write_text(json.dumps(value), encoding='utf-8')
        review = server_transfer.transfer_scope(config)
        paths = sorted(item['path'] for item in review['scope']['projects'])
        self.assertEqual(paths, sorted([str(self.site.resolve()), str(self.private.resolve())]))
        self.assertIn('*.log в корне данных', [row['path'] for row in review['host_exclusions']])
        chosen = server_transfer.chosen_projects(config, [{'path': str(self.site), 'bring': True},
                                                          {'path': str(self.private), 'bring': False}])
        self.assertEqual([Path(path).resolve() for path in chosen], [self.site.resolve()])
        with self.assertRaises(server_transfer.TransferError):
            server_transfer.chosen_projects(config, [{'path': str(self.root / 'elsewhere'), 'bring': True}])

    def test_a_project_inside_the_data_is_not_a_separate_project(self):
        with self.assertRaisesRegex(ValueError, 'вне данных'):
            self.export(projects=[self.data / 'workspace/notes'])


if __name__ == '__main__':
    unittest.main()
