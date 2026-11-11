"""Install a new server agent and update an installed one from the window (11.11.2026).

Yegor: «установка серверного агента непосредственно из приложения и обновление непосредственно
из приложения, без пользовательского запуска отдельных ручных скриптов». He chose (11.11) that the
window finds a new server agent through its Telegram bot menu, as after a transfer, and that a
server without an updater is updated over SSH from the window.

The Linux class drives the real SSH path (host-key confirmation, SFTP, exec) against a disposable
fixture whose install.sh and channel are stand-ins: no Docker build, no Telegram, no owner bot.
"""
import http.server
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import tarfile
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'localharness'), str(Path(__file__).resolve().parents[1])]
import server_install  # noqa: E402
import server_transfer as transfer  # noqa: E402

DESK = Path(__file__).resolve().parents[1]
TOKEN = '123456789:' + 'A' * 35


def fake_program(root: Path, version: str) -> Path:
    program = root / 'program'
    for folder in ('app', 'tree', 'server'):
        (program / folder).mkdir(parents=True, exist_ok=True)
    (program / 'app/deskapp.py').write_text('# fixture\n')
    (program / 'tree/agent.py').write_text('# fixture\n')
    (program / 'requirements.txt').write_text('')
    (program / 'helene-build.json').write_text(json.dumps({'version': version}))
    (program / 'server/helene.server.json').write_bytes((DESK / 'server/helene.server.json').read_bytes())
    # Stand-in for server/install.sh: what the real one leaves behind, without Docker.
    (program / 'server/install.sh').write_text(
        'set -eu\n'
        'mkdir -p "$HELENE_DIR/data/.serverboot" "$HELENE_DIR/server/updater"\n'
        'cp helene-build.json "$HELENE_DIR/helene-build.json"\n'
        'printf fixture-window-key > "$HELENE_DIR/data/.serverboot/desk-token"\n'
        ': > "$HELENE_DIR/server/updater/updater.py"\n'
        'echo "$HELENE_PROJECT $HELENE_CONTAINER $HELENE_PORT" > "$HELENE_DIR/.install-env"\n')
    return program


class Offline(unittest.TestCase):
    def test_bot_and_owner_are_required_and_validated(self):
        for raw in ({}, {'telegram': {'bot_token': 'nope', 'owner_id': '1'}},
                    {'telegram': {'bot_token': TOKEN, 'owner_id': 'x'}},
                    {'telegram': {'bot_token': TOKEN, 'owner_id': '0'}}):
            with self.assertRaises(server_install.InstallError):
                server_install.telegram_fields(raw)
        self.assertEqual(server_install.telegram_fields({'telegram': {'bot_token': TOKEN, 'owner_id': ' 42 '}}),
                         {'bot_token': TOKEN, 'owner_id': 42})

    def test_server_config_is_the_shipped_template_with_this_bot(self):
        with tempfile.TemporaryDirectory() as tmp:
            program = fake_program(Path(tmp), '9.9.9')
            cfg = server_install.server_config(program, {'bot_token': TOKEN, 'owner_id': 42}, 'Вера')
            template = json.loads((DESK / 'server/helene.server.json').read_text(encoding='utf-8'))
            self.assertEqual(cfg['telegram'], {**template['telegram'], 'bot_token': TOKEN, 'owner_id': 42})
            self.assertEqual(cfg['agent']['name'], 'Вера')
            self.assertEqual(cfg['phone'], template['phone'], 'automatic phone keeps the bot menu current')
            self.assertEqual({k: v for k, v in cfg.items() if k not in ('telegram', 'agent')},
                             {k: v for k, v in template.items() if k not in ('telegram', 'agent')})

    def test_channel_readiness_needs_version_supervisor_and_channel(self):
        good = {'/api/state': {'desk': {'version': '2.6.0'}},
                '/api/supervisor': {'kind': 'serverboot', 'alive': True, 'children': [{'id': 'channel', 'alive': True}]}}
        self.assertTrue(server_install.channel_ready(good, '2.6.0'))
        self.assertFalse(server_install.channel_ready(good, '2.6.1'))
        self.assertFalse(server_install.channel_ready({**good, '/api/supervisor': {'kind': 'serverboot', 'alive': True}}, '2.6.0'))

    def test_arm_kit_carries_no_x86_relay(self):
        with tempfile.TemporaryDirectory() as tmp:
            program = fake_program(Path(tmp), '9.9.9')
            (program / 'helene-relay').write_bytes(b'\x7fELF' + b'\0' * 60)
            for arch, expected in (('x86_64', True), ('aarch64', False)):
                kit = Path(tmp) / f'{arch}.tar.gz'
                transfer.make_program(program, kit, arch=arch)
                with tarfile.open(kit) as archive:
                    self.assertEqual('helene-relay' in archive.getnames(), expected, arch)

    def test_new_agent_becomes_its_own_remote_profile(self):
        from localharness import agents, python_desktop_commands as commands
        from localharness import server_install as installer
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'helene.json').write_text(json.dumps({'mode': 'local', 'telegram': {'proxy': {'enabled': True, 'url': 'socks5://p'}}}))
            local = agents.find(root, agents.BASE_ID)
            owner = types.SimpleNamespace(root=root, transfer_lock=threading.Lock(), refreshed=0,
                                          current=lambda: local)
            owner.refresh = lambda: setattr(owner, 'refreshed', owner.refreshed + 1)
            installed = {'ok': True, 'key': 'window-key', 'version': '2.6.0', 'telegram': {'bot_token': TOKEN, 'owner_id': 42},
                         'name': 'Вера', 'arch': 'aarch64', 'id': 'r1'}

            def fake_install(raw, config_path, program):
                installer.save(config_path, {'id': 'r1', 'phase': 'installed', 'version': '2.6.0'})
                return installed
            with patch.object(installer, 'install_new', side_effect=fake_install), \
                    patch.object(installer, 'locate', return_value='https://new.example') as locate:
                result = commands.invoke(owner, 'server_install', {'action': 'install', 'request': '{}'})
            self.assertTrue(result['ok'])
            self.assertEqual(locate.call_args.args[:2], ('window-key', {'bot_token': TOKEN, 'owner_id': 42}))
            created = agents.find(root, result['agent_id'])
            cfg = agents.read_config(created.config)
            self.assertEqual((cfg['mode'], cfg['base'], cfg['key'], cfg['server_locator']),
                             ('remote', 'https://new.example', 'window-key', 'telegram-menu'))
            self.assertEqual(cfg['telegram']['bot_token'], TOKEN)
            self.assertEqual(cfg['telegram']['proxy']['url'], 'socks5://p', 'the local Telegram proxy is reused')
            self.assertEqual(json.loads((root / 'helene.json').read_text())['mode'], 'local', 'local agent untouched')
            receipt = installer.saved(local.config)
            self.assertEqual(receipt['phase'], 'ready')
            self.assertNotIn('key', receipt, 'the window key leaves the receipt once the profile holds it')
            self.assertEqual(owner.refreshed, 1)


@unittest.skipUnless(sys.platform.startswith('linux') and os.geteuid() == 0,
                     'actual SSH install stand runs as root in disposable Linux')
class OverSSH(unittest.TestCase):
    def setUp(self):
        import t_server_ssh_1010 as fixture
        self.fixture = fixture.SSH('test_probe_sends_no_password_before_host_confirmation_and_verifies_real_server')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.tmp = Path(self.fixture.root)
        # The fixture's SFTP admits only its root; an installation must live in /opt (the
        # destination rule). This disposable container lets the fixture serve /opt.
        self.fixture.root = '/opt'
        identity = 'helene-install-fixture-' + secrets.token_hex(5)
        self.directory = '/opt/' + identity
        self.addCleanup(lambda: os.system('rm -rf ' + self.directory + '*'))
        port = 20000 + secrets.randbelow(20000)
        self.raw = {**self.fixture.raw, 'fingerprint': transfer.fingerprint(self.fixture.key), 'confirmed_host': True,
                    'deployment': {'directory': self.directory, 'project': identity, 'container': identity, 'port': port},
                    'telegram': {'bot_token': TOKEN, 'owner_id': '42'}, 'agent_name': 'Вера'}
        # Docker stand-in on PATH: present, compose present, no such container yet, updater answers.
        bin_dir = self.tmp / 'bin'; bin_dir.mkdir()
        (bin_dir / 'docker').write_text('#!/bin/sh\ncase "$1" in info) echo 27.0;; compose) echo 2.29;; '
                                        'inspect) exit 1;; exec) exit 0;; *) exit 0;; esac\n')
        (bin_dir / 'docker').chmod(0o755)
        os.environ['PATH'] = str(bin_dir) + os.pathsep + os.environ['PATH']
        self.version = '9.9.9'
        self.served = {'version': self.version}

        class Channel(http.server.BaseHTTPRequestHandler):
            def do_GET(inner):
                if inner.headers.get('Authorization') != 'Bearer fixture-window-key':
                    inner.send_response(403); inner.end_headers(); return
                body = {'/api/state': {'desk': {'version': self.served['version']}, 'agent': None},
                        '/api/supervisor': {'kind': 'serverboot', 'alive': True, 'children': [{'id': 'channel', 'alive': True}]},
                        '/api/panic': {'stopped': False}}.get(inner.path)
                inner.send_response(200 if body is not None else 404); inner.end_headers()
                inner.wfile.write(json.dumps(body or {}).encode())

            def log_message(inner, *args):
                pass
        # The real channel exists only after install.sh; so does this stand-in.
        original = server_install._install_script

        def install_then_channel(*args, **kwargs):
            out = original(*args, **kwargs)
            if not getattr(self, 'http', None):
                self.http = http.server.ThreadingHTTPServer(('127.0.0.1', port), Channel)
                threading.Thread(target=self.http.serve_forever, daemon=True).start()
                self.addCleanup(self.http.server_close); self.addCleanup(self.http.shutdown)
            return out
        hook = patch.object(server_install, '_install_script', side_effect=install_then_channel)
        hook.start(); self.addCleanup(hook.stop)
        self.config = self.tmp / 'local' / 'helene.json'; self.config.parent.mkdir(); self.config.write_text('{}')

    def test_fresh_install_writes_bot_config_privately_and_returns_the_window_key(self):
        program = fake_program(self.tmp, self.version)
        result = server_install.install_new(self.raw, self.config, program)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['key'], 'fixture-window-key')
        directory = Path(self.directory)
        cfg = json.loads((directory / 'helene.json').read_text(encoding='utf-8'))
        self.assertEqual(cfg['telegram'], {'bot_token': TOKEN, 'owner_id': 42})
        self.assertEqual(cfg['agent']['name'], 'Вера')
        self.assertEqual(stat.S_IMODE((directory / 'helene.json').stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(json.loads((directory / '.window-install.json').read_text())['id'], result['id'])
        self.assertIn(self.raw['deployment']['project'], (directory / '.install-env').read_text())
        self.assertFalse(any(p.name.startswith(directory.name + '.install-') for p in directory.parent.iterdir()),
                         'the upload stage is removed')
        self.assertEqual(server_install.saved(self.config)['phase'], 'installed')
        self.assertNotIn(self.fixture.password, json.dumps(server_install.saved(self.config)))

    def test_an_occupied_folder_is_never_installed_over(self):
        Path(self.directory).mkdir(parents=True); (Path(self.directory) / 'foreign.txt').write_text('x')
        with self.assertRaises(server_install.InstallError):
            server_install.install_new(self.raw, self.config, fake_program(self.tmp, self.version))
        self.assertEqual(sorted(p.name for p in Path(self.directory).iterdir()), ['foreign.txt'])

    def test_update_brings_an_installed_server_to_this_version(self):
        program = fake_program(self.tmp, self.version)
        directory = Path(self.directory); directory.mkdir(parents=True)
        (directory / 'helene.json').write_text('{}')
        (directory / 'helene-build.json').write_text(json.dumps({'version': '1.0.0'}))
        result = server_install.update(self.raw, program)
        self.assertTrue(result['ok'], result)
        self.assertEqual((result['before'], result['after']), ('1.0.0', self.version))
        self.assertTrue(result['updater'])
        self.assertIn('обновлён', result['message'])


if __name__ == '__main__':
    unittest.main()
