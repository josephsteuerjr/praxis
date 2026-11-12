"""Full return of a transferred agent: fresh server life, nothing silently dropped.

Server steps run the uploaded helper functions directly on a disposable root
(Linux only: the helper uses flock like install.sh). Local steps stage and
activate the payload with the real carry modules and codecarry. No SSH, no
Docker, no owner data: containers are an explicit fake of `docker` output.
"""
import json, os, shutil, subprocess, sys, tempfile, types, unittest, zipfile
from pathlib import Path
from unittest import mock

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / 'localharness'), str(DESK / 'server')]
try:
    import paramiko  # noqa: F401
except ImportError:  # WSL test host: no SSH happens in these tests
    fake = types.ModuleType('paramiko')
    fake.MissingHostKeyPolicy = object
    fake.SSHException = type('SSHException', (Exception,), {})
    sys.modules['paramiko'] = fake
from localharness import carry_payload, server_return as back  # noqa: E402
from localharness import carry_scope  # noqa: E402

LINUX = os.name == 'posix'
GIT = shutil.which('git')
CLEAN = {'tree/agent.py': 'agent v1\n', 'tree/a.py': 'a clean\n', 'tree/b.py': 'b clean\n',
         'tree/c.py': 'c clean\n', 'app/deskapp.py': 'app clean\n', 'app/static/index.html': 'ui\n'}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())


def link(path, target):
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(path), str(target)], check=True, capture_output=True)
    else:
        path.symlink_to(target, target_is_directory=True)


def git(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@e',
               GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@e')
    return subprocess.run([GIT, *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


class Scenario:
    """A local programme whose agent moved, and that agent's later life on a server."""

    def __init__(self, root):
        self.root = root
        self.program = root / 'program'
        for name, text in CLEAN.items():
            write(self.program / name, text)
        write(self.program / 'tree/a.py', 'a edited before move\n')
        write(self.program / 'tree/c.py', 'c edited before move\n')
        write(self.program / 'helene-build.json', json.dumps({'version': '2.7.0', 'desk': {'flavor': 'windows'}}))
        self.data = root / 'agent-data'
        write(self.data / 'memory/story.md', 'before the move\n')
        self.forward = root / 'forward.zip'
        with zipfile.ZipFile(self.forward, 'w') as archive:
            archive.writestr('code/manifest.json', json.dumps(
                {'version': '2.7.0', 'edited': ['tree/a.py', 'tree/c.py'], 'whole': False}))
            archive.writestr('code/mine/tree/a.py', 'a edited before move\n')
            archive.writestr('code/mine/tree/c.py', 'c edited before move\n')
        self.config = self.program / 'helene.json'
        # After the move the window watches the server copy (remote mode).
        write(self.config, json.dumps({'mode': 'remote', 'base': 'https://agent.example', 'key': 'window-key',
                                       'server_locator': 'telegram-menu', 'tree': str(self.data), 'code': 'tree',
                                       'app': 'app/deskapp.py', 'port': 8094, 'agent': {'name': 'Jarvis'},
                                       'model': {'model': 'sol'}, 'phone': {'enabled': True}}))
        self.row = {'id': '0' * 24, 'archive': str(self.forward), 'archive_sha': 'ab' * 32, 'phase': 'ready',
                    'destination': ['203.0.113.5', 22, 'root'], 'fingerprint': 'SHA256:pinned',
                    'version': '2.7.0', 'deployment': {'directory': '/srv/helene-test', 'project': 'helene',
                                                       'container': 'helene', 'port': 8094}}
        write(self.data / 'memory/.state/server-transfer.json', json.dumps(self.row))
        self.raw = {'host': '203.0.113.5', 'port': 22, 'login': 'root', 'deployment': self.row['deployment']}

    def server(self):
        """The agent's life after the move, in the server installation layout."""
        srv = self.root / 'srv'
        install = srv / 'helene'
        for name, text in CLEAN.items():
            write(install / name, text)
            write(install / '.updater/pristine/2.7.0' / name, text)
        write(install / 'tree/a.py', 'a edited before move\n')    # kept
        write(install / 'tree/b.py', 'b edited on the server\n')  # new edit
        write(install / 'tree/c.py', 'c clean\n')                 # reverted on the server
        write(install / 'tree/new_tool.py', 'added on the server\n')
        write(install / 'app/deskapp.py', 'app edited on the server\n')
        write(install / 'helene-build.json', json.dumps({'version': '2.7.0'}))
        data = install / 'data'
        write(data / 'memory/story.md', 'before the move\nand after it, on the server\n')
        write(data / 'memory/learned.md', 'learned on the server\n')
        write(data / 'workspace/big.bin', b'0123456789abcdef' * (600 * 1024))
        write(data / 'workspace/run.log', 'project log\n')
        (data / 'workspace/empty').mkdir(parents=True)
        write(data / 'memory/.state/harness.lock', 'server process')
        write(data / '.serverboot/desk-token', 'server window key')
        write(data / 'models/whisper.bin', b'model')
        write(data / 'memory/.state/phone-access/telegram-menu.json', '{}')
        shared, other = srv / 'shared', srv / 'other'
        write(shared / 'notes.md', 'shared project\n')
        write(other / 'secret.md', 'stays on the server\n')
        link(data / 'workspace/mnt/shared', shared)
        link(data / 'workspace/mnt/other', other)
        link(data / 'workspace/current', data / 'workspace/empty')
        if GIT:
            repo = data / 'workspace/proj'
            write(repo / 'README.md', 'one\n')
            git(repo, 'init', '-q', '-b', 'main')
            git(repo, 'add', '.')
            git(repo, 'commit', '-q', '-m', 'first')
            write(repo / 'README.md', 'two\n')
            git(repo, 'commit', '-q', '-am', 'second on the server')
            git(repo, 'worktree', 'add', '-q', '-b', 'side', str(data / 'workspace/side'))
            write(data / 'workspace/side/dirty.txt', 'untracked in worktree\n')
        write(install / 'helene.json', json.dumps({'mode': 'local', 'tree': 'data', 'code': 'tree',
                                                   'app': 'app/deskapp.py', 'port': 8094,
                                                   'agent': {'name': 'Jarvis'}, 'model': {'model': 'astra-switched'},
                                                   'sandbox': {'mounts': [str(shared)]}}))
        mark = {'id': self.row['id'], 'archive_sha': self.row['archive_sha']}
        write(install / '.window-transfer.json', json.dumps(mark))
        write(install / '.window-transfer-stopped', json.dumps(mark))
        server_row = {**self.row, 'deployment': {**self.row['deployment'], 'directory': str(install)}}
        return install, server_row, shared, other


def docker_without_containers(args):
    if args[:2] == ['container', 'ls']:
        return ''
    raise AssertionError('no container expected: ' + str(args))


@unittest.skipUnless(LINUX, 'server helper uses flock, like install.sh')
class ServerSnapshot(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.scene = Scenario(Path(temp.name).resolve())
        self.install, self.row, self.shared, self.other = self.scene.server()
        import transfer_return
        self.helper = transfer_return
        patcher = mock.patch.object(transfer_return.ownership, 'command', docker_without_containers)
        patcher.start()
        self.addCleanup(patcher.stop)

    def plan(self, bring_other=False):
        return {'return_id': '1234567890abcdef', 'projects': [
            {'path': str(self.shared), 'bring': True}, {'path': str(self.other), 'bring': bring_other}]}

    def test_scope_names_external_projects_and_host_exclusions_without_changes(self):
        reply = self.helper.scope(self.row)
        projects = sorted(item['path'] for item in reply['scope']['projects'])
        self.assertEqual(projects, sorted([str(self.shared), str(self.other)]))
        self.assertEqual(reply['version'], '2.7.0')
        self.assertIn('.serverboot', [row['path'] for row in reply['host_exclusions']])
        self.assertFalse(Path(str(self.install) + '.return-1234567890abcdef').exists())

    def test_every_external_project_needs_a_decision(self):
        plan = {'return_id': '1234567890abcdef', 'projects': [{'path': str(self.shared), 'bring': True}]}
        with self.assertRaisesRegex(ValueError, 'owner decision'):
            self.helper.snapshot(self.row, plan)

    def test_snapshot_requires_fence_and_stopped_containers(self):
        (self.install / '.window-transfer-stopped').unlink()
        with self.assertRaisesRegex(ValueError, 'not fenced'):
            self.helper.snapshot(self.row, self.plan())
        write(self.install / '.window-transfer-stopped', json.dumps({'id': self.row['id'], 'archive_sha': self.row['archive_sha']}))
        identity = 'f' * 64

        def running(args):
            if args[:2] == ['container', 'ls']:
                return identity if args[4].endswith('helene$') else ''
            return json.dumps([{'Id': identity, 'State': {'Running': True},
                                'Config': {'Labels': {'com.docker.compose.project': 'helene'}},
                                'Mounts': [{'Source': str(self.install)}]}])
        with mock.patch.object(self.helper.ownership, 'command', running):
            with self.assertRaisesRegex(ValueError, 'running'):
                self.helper.snapshot(self.row, self.plan())

    def test_snapshot_is_complete_explicit_and_reused(self):
        first = self.helper.snapshot(self.row, self.plan())
        self.assertFalse(first['reused'])
        stage = Path(str(self.install) + '.return-1234567890abcdef')
        self.assertEqual(Path(first['archive']), stage / 'payload.zip')
        checked = carry_payload.inspect(first['archive'])
        names = set(zipfile.ZipFile(first['archive']).namelist())
        self.assertIn('roots/data/workspace/big.bin', names)
        self.assertIn('roots/data/workspace/run.log', names)
        self.assertIn('roots/data/workspace/empty/', names)
        self.assertIn('roots/code/new_tool.py', names)
        self.assertIn('roots/project-1/notes.md', names)
        for host in ('roots/data/.serverboot/desk-token', 'roots/data/models/whisper.bin',
                     'roots/data/memory/.state/harness.lock', 'roots/data/memory/.state/phone-access/telegram-menu.json'):
            self.assertNotIn(host, names)
        reasons = {row['path']: row['reason'] for row in checked['exclusions']}
        self.assertIn('проект оставлен на сервере', reasons['workspace/mnt/other'])
        self.assertTrue(all(row['reason'] for row in checked['exclusions']))
        self.assertEqual(checked['metadata']['config']['model']['model'], 'astra-switched')
        again = self.helper.snapshot(self.row, self.plan())
        self.assertTrue(again['reused'])
        self.assertEqual(again['sha256'], first['sha256'])
        changed = self.helper.snapshot(self.row, self.plan(bring_other=True))
        self.assertFalse(changed['reused'])
        names = zipfile.ZipFile(changed['archive']).namelist()
        # Roots are numbered by server path; both projects are now in the archive.
        self.assertEqual(sorted(name.split('/', 2)[2] for name in names
                                if name.startswith('roots/project-') and name.count('/') == 2 and not name.endswith('/')),
                         ['notes.md', 'secret.md'])

    def departure_kit(self):
        """The programme kit uploaded at the move: local code with its pre-move edits."""
        import tarfile
        stage = Path(str(self.install) + '.transfer-' + self.row['id'])
        stage.mkdir()
        with tarfile.open(stage / 'program.tar.gz', 'w:gz') as kit:
            for name in ('tree', 'app'):
                kit.add(self.scene.program / name, arcname=name)
        self.row['program_sha'] = self.helper.kit_signature(stage / 'program.tar.gz')
        self.scene.row['program_sha'] = self.row['program_sha']

    def test_tampered_kit_is_not_a_merge_base(self):
        self.departure_kit()
        self.row['program_sha'] = '0' * 64
        snap = self.helper.snapshot(self.row, self.plan())
        self.assertNotIn('departure', snap['roots'])

    @unittest.skipUnless(GIT, 'git is required for the worktree fixture')
    def test_round_trip_returns_fresh_life_code_projects_and_git(self):
        self.departure_kit()
        snap = self.helper.snapshot(self.row, self.plan())
        self.assertIn('departure', snap['roots'])
        scene = self.scene
        state = {'id': '1234567890abcdef', 'phase': 'downloaded', 'projects': self.plan()['projects'],
                 'remote': {k: snap[k] for k in ('archive', 'sha256', 'bytes', 'version', 'exclusions')}}
        row = {**scene.row, 'return': state}
        write(scene.data / 'memory/.state/server-transfer.json', json.dumps(row))
        folder = back.places(scene.config, state['id'])['payload']
        folder.mkdir(parents=True)
        shutil.copy2(snap['archive'], folder / 'payload.zip')
        with mock.patch.object(back.transfer.owner_stop, 'stopped', return_value=True):
            result = back.run(scene.raw, scene.config, scene.program, projects=self.plan()['projects'])
        returned = Path(result['data'])
        self.assertEqual((returned / 'memory/learned.md').read_text(), 'learned on the server\n')
        self.assertIn('on the server', (returned / 'memory/story.md').read_text())
        self.assertEqual((returned / 'workspace/big.bin').stat().st_size, 16 * 600 * 1024)
        self.assertTrue((returned / 'workspace/empty').is_dir())
        self.assertEqual((returned / 'workspace/mnt/shared/notes.md').read_text(), 'shared project\n')
        self.assertFalse((returned / 'workspace/mnt/other').exists())
        self.assertFalse((returned / '.serverboot').exists())
        # Git: history and the worktree's own state are independent of the server paths.
        self.assertEqual(git(returned / 'workspace/proj', 'log', '-1', '--format=%s'), 'second on the server')
        self.assertTrue((returned / 'workspace/side/.git').is_dir())
        self.assertEqual((returned / 'workspace/side/dirty.txt').read_text(), 'untracked in worktree\n')
        # Code: kept edit, server edit, server revert, server addition; UI build untouched.
        tree = scene.program / 'tree'
        self.assertEqual((tree / 'a.py').read_text(), 'a edited before move\n')
        self.assertEqual((tree / 'b.py').read_text(), 'b edited on the server\n')
        self.assertEqual((tree / 'c.py').read_text(), 'c clean\n')
        self.assertEqual((tree / 'new_tool.py').read_text(), 'added on the server\n')
        self.assertEqual((scene.program / 'app/deskapp.py').read_text(), 'app edited on the server\n')
        self.assertEqual(result['code']['base'], 'departure')
        backup = Path(result['code']['local_before'])
        self.assertEqual((backup / 'tree/c.py').read_text(), 'c edited before move\n')
        # Config, receipts and the retained previous copy.
        config = json.loads(scene.config.read_text())
        self.assertEqual(Path(config['tree']), returned)
        self.assertEqual(config['model']['model'], 'astra-switched')
        self.assertEqual(config['phone'], {'enabled': True})
        fresh = json.loads((returned / 'memory/.state/server-transfer.json').read_text())
        self.assertEqual(fresh['phase'], 'server-stopped')
        self.assertEqual(fresh['returned']['sha256'], snap['sha256'])
        retired = json.loads((scene.data / 'memory/.state/server-transfer.json').read_text())
        self.assertEqual(retired['phase'], 'returned-retired')
        self.assertEqual((scene.data / 'memory/story.md').read_text(), 'before the move\n')
        with self.assertRaisesRegex(back.ReturnError, 'уже вернулся'):
            back.run(scene.raw, scene.config, scene.program, projects=self.plan()['projects'])


class LocalActivation(unittest.TestCase):
    """Windows and Linux: a payload in the server format, staged and activated here."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.scene = Scenario(Path(temp.name).resolve())
        server = Path(temp.name) / 'server'
        for name, text in CLEAN.items():
            write(server / name.replace('tree/', 'code/'), text)
            write(server / 'base' / name, text)
        write(server / 'code/a.py', 'a edited before move\n')
        write(server / 'code/b.py', 'b edited on the server\n')
        write(server / 'data/memory/learned.md', 'learned on the server\n')
        write(server / 'data/workspace/notes/one.md', 'note\n')
        link(server / 'data/workspace/latest', server / 'data/workspace/notes')
        self.config_on_server = {'agent': {'name': 'Jarvis'}, 'model': {'model': 'astra-switched'}, 'tree': 'data'}
        archive = Path(temp.name) / 'built.zip'
        self.receipt = carry_payload.create(archive, {'data': server / 'data', 'code': server / 'code',
                                                      'app': server / 'app', 'base': server / 'base'},
                                            metadata={'version': '2.7.0', 'config': self.config_on_server, 'git': []})
        self.state = {'id': 'fedcba9876543210', 'phase': 'downloaded', 'projects': [],
                      'remote': {'sha256': self.receipt['sha256'], 'bytes': archive.stat().st_size,
                                 'archive': '/srv/x/payload.zip', 'version': '2.7.0', 'exclusions': []}}
        folder = back.places(self.scene.config, self.state['id'])['payload']
        folder.mkdir(parents=True)
        shutil.copy2(archive, folder / 'payload.zip')
        write(self.scene.data / 'memory/.state/server-transfer.json', json.dumps({**self.scene.row, 'return': self.state}))
        patcher = mock.patch.object(back.transfer.owner_stop, 'stopped', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_return(self):
        return back.run(self.scene.raw, self.scene.config, self.scene.program, projects=[])

    def test_activation_switches_only_the_config_and_keeps_previous_copy(self):
        result = self.run_return()
        returned = Path(result['data'])
        self.assertEqual((returned / 'memory/learned.md').read_text(), 'learned on the server\n')
        self.assertEqual((returned / 'workspace/latest/one.md').read_text(), 'note\n')
        self.assertTrue(returned.is_relative_to(self.scene.data.parent))
        self.assertEqual((self.scene.program / 'tree/b.py').read_text(), 'b edited on the server\n')
        self.assertEqual((self.scene.program / 'tree/c.py').read_text(), 'c clean\n')
        self.assertEqual(result['code']['base'], 'pristine+departure')
        self.assertEqual((self.scene.data / 'memory/story.md').read_text(), 'before the move\n')
        config = json.loads(self.scene.config.read_text())
        self.assertEqual(Path(config['tree']), returned)
        self.assertEqual(config['code'], 'tree')
        self.assertEqual(config['mode'], 'local')
        self.assertFalse({'base', 'key', 'server_locator'} & set(config))
        self.assertEqual(carry_scope.read_config(Path(result['code']['local_before']).parent / 'config-before.json')['tree'],
                         str(self.scene.data))

    def test_newer_server_version_waits_for_a_local_update_without_switching(self):
        write(self.scene.program / 'helene-build.json', json.dumps({'version': '2.6.9'}))
        with self.assertRaisesRegex(back.ReturnError, 'обнови программу'):
            self.run_return()
        config = json.loads(self.scene.config.read_text())
        self.assertEqual(Path(config['tree']), self.scene.data)
        row = json.loads((self.scene.data / 'memory/.state/server-transfer.json').read_text())
        self.assertEqual(row['return']['phase'], 'staged')
        write(self.scene.program / 'helene-build.json', json.dumps({'version': '2.7.0'}))
        result = self.run_return()
        self.assertEqual(Path(json.loads(self.scene.config.read_text())['tree']), Path(result['data']))

    def test_interruption_before_the_config_switch_converges_on_retry(self):
        original = back.transfer.carry._write_config

        def broken(path, value):
            raise OSError('power loss before the config switch')
        with mock.patch.object(back.transfer.carry, '_write_config', broken):
            with self.assertRaises(OSError):
                self.run_return()
        self.assertEqual(Path(json.loads(self.scene.config.read_text())['tree']), self.scene.data)
        row = json.loads((self.scene.data / 'memory/.state/server-transfer.json').read_text())
        self.assertEqual(row['return']['phase'], 'activating')
        self.assertIs(back.transfer.carry._write_config, original)
        result = self.run_return()
        self.assertEqual((self.scene.program / 'tree/b.py').read_text(), 'b edited on the server\n')
        self.assertEqual(Path(json.loads(self.scene.config.read_text())['tree']), Path(result['data']))

    def test_cancel_before_activation_keeps_bytes_and_fence(self):
        reply = back.cancel(self.scene.config)
        self.assertTrue(reply['cancelled'])
        row = json.loads((self.scene.data / 'memory/.state/server-transfer.json').read_text())
        self.assertNotIn('return', row)
        self.assertEqual(row['phase'], 'ready')
        self.assertTrue((back.places(self.scene.config, self.state['id'])['payload'] / 'payload.zip').is_file())

    def test_versions_compare_numerically(self):
        self.assertTrue(back.compatible('2.7.0', '2.7.0'))
        self.assertTrue(back.compatible('2.6.1', '2.10.0'))
        self.assertFalse(back.compatible('2.10.0', '2.9.9'))
        self.assertFalse(back.compatible('', ''))


class Download(unittest.TestCase):
    """A broken connection resumes by offset; only the exact bytes are accepted."""

    class Remote:
        def __init__(self, data, fail_after=None):
            self.data, self.fail_after = data, fail_after

        def open_sftp(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def stat(self, path):
            return types.SimpleNamespace(st_size=len(self.data))

        def open(self, path, mode):
            remote = self

            class Handle:
                position = 0

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def seek(self, offset):
                    self.position = offset

                def prefetch(self, size, max_concurrent_requests=None):
                    assert max_concurrent_requests, 'read-ahead must be bounded'

                def read(self, size):
                    if remote.fail_after is not None and self.position >= remote.fail_after:
                        raise EOFError('connection lost')
                    block = remote.data[self.position:self.position + min(size, 1024 * 1024)]
                    self.position += len(block)
                    return block
            return Handle()

    def test_resume_after_a_break_and_reject_wrong_bytes(self):
        import hashlib
        data = os.urandom(3 * 1024 * 1024 + 17)
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'payload'
            with self.assertRaises(EOFError):
                back.download(self.Remote(data, fail_after=2 * 1024 * 1024), '/x', len(data), digest, folder)
            self.assertEqual((folder / 'payload.zip.part').stat().st_size, 2 * 1024 * 1024)
            final = back.download(self.Remote(data), '/x', len(data), digest, folder)
            self.assertEqual(final.read_bytes(), data)
            final.unlink()
            with self.assertRaisesRegex(back.ReturnError, 'не совпал'):
                back.download(self.Remote(b'x' * len(data)), '/x', len(data), digest, folder)
            self.assertFalse((folder / 'payload.zip').exists())


if __name__ == '__main__':
    unittest.main()
