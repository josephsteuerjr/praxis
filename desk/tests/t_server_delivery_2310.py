"""Server delivery contracts, runnable on Windows without Docker or live state."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK / "server"), str(DESK / "server/updater"), str(DESK / "localharness")]
import serverboot
import updater
import deskctl


class ServerDelivery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.tree = self.base / "data"
        self.tree.mkdir()
        (self.base / "helene-relay").write_bytes(b"\x7fELFfixture")

    def relay(self, config, env=None):
        with mock.patch.object(serverboot, "_port_busy", return_value=False), mock.patch.dict(
                serverboot.os.environ, {}, clear=True):
            return serverboot.relay_child(self.base, config, self.tree, env or {})

    def test_embedded_cli_auth_and_private_network_contract(self):
        config = {"relay": {"enabled": True, "port": 5312, "key": "fixture-relay",
                            "proxy": {"mode": "proxy", "protocol": "socks5h", "host": "proxy.invalid",
                                      "port": 1080, "username": "u@name", "password": "p:word"}}}
        child = self.relay(config, {"HELENE_TOKEN": "fixture-channel", "RELAY_API_KEY": "ambient"})
        self.assertEqual(child.argv, [str(self.base / "helene-relay"), "serve"])
        self.assertEqual(child.cwd, self.tree / "relay")
        self.assertEqual(child.env["RELAY_AUTH_DIR"], str(child.cwd / "local_auth"))
        self.assertEqual(child.env["RELAY_API_KEY"], "fixture-relay")
        self.assertNotIn("HELENE_TOKEN", child.env)
        self.assertEqual(child.env["RELAY_NETWORK_MODE"], "proxy")
        self.assertEqual(child.env["RELAY_PROXY_URL"], "socks5h://u%40name:p%3Aword@proxy.invalid:1080")
        source = DESK.parent / "_state/release-1.9.1/relay-src/src/main.rs"
        if source.is_file():
            self.assertIn('"serve" | "server" | "run"', source.read_text(encoding="utf-8"))

    def test_image_only_relay_does_not_borrow_provider_or_ambient_keys(self):
        child = self.relay({"images": {"enabled": True}, "model": {"key": "provider"},
                            "relay": {"proxy": {"mode": "direct"}}},
                           {"RELAY_API_KEY": "ambient", "RELAY_PROXY_URL": "ambient", "HELENE_TOKEN": "channel"})
        self.assertNotIn("RELAY_API_KEY", child.env)
        self.assertNotIn("HELENE_TOKEN", child.env)
        self.assertEqual(child.env["RELAY_NETWORK_MODE"], "direct")
        self.assertEqual(child.env["RELAY_PROXY_URL"], "")

    def test_invalid_proxy_refuses_without_echoing_password(self):
        with self.assertRaises(ValueError) as error:
            self.relay({"relay": {"enabled": True, "proxy": {"mode": "proxy", "host": "bad/path",
                         "port": 1, "password": "fixture-secret"}}})
        self.assertNotIn("fixture-secret", str(error.exception))

    def test_optional_xtts_is_owned_and_has_no_model_or_channel_secrets(self):
        python = self.base / "voice-python"
        python.write_bytes(b"fixture")
        worker = self.base / "app/localharness/speech_worker.py"
        worker.parent.mkdir(parents=True)
        worker.write_bytes((DESK / "localharness/speech_worker.py").read_bytes())
        worker.with_name("xtts_tts_worker.py").write_bytes((DESK.parent / "helene/core/xtts_tts_worker.py").read_bytes())
        model = self.tree / "models/xtts"
        model.mkdir(parents=True)
        for name in ("config.json", "model.pth", "vocab.json", "speakers_xtts.pth"):
            (model / name).write_bytes(b"fixture")
        child = serverboot.xtts_child(self.base, {"voice": {"speak": True}}, self.tree,
                {"HELENE_XTTS_PYTHON": str(python), "HELENE_TOKEN": "channel", "RELAY_API_KEY": "relay"},
                user=10001, group=10000)
        self.assertEqual(child.key, "xtts")
        self.assertEqual(child.user, 10001)
        self.assertEqual(child.argv[:3], [str(python), "-u", str(worker)])
        self.assertEqual(child.argv[child.argv.index("--tree") + 1], str(self.tree))
        self.assertEqual(child.argv[-2:], ["--device", "cpu"])
        self.assertNotIn("HELENE_TOKEN", child.env)
        self.assertNotIn("RELAY_API_KEY", child.env)
        self.assertEqual(child.env["HELENE_SUPERVISOR"], "serverboot")
        self.assertEqual(child.env["HELENE_TREE"], str(self.tree))
        with mock.patch.object(serverboot.subprocess, "Popen") as spawn:
            child.spawn()
            child.spawn()  # physical retry must not reuse an IPC generation
        first, second = spawn.call_args_list
        self.assertNotEqual(first.args[0][-1], second.args[0][-1])
        for call in (first, second):
            argv = call.args[0]
            self.assertEqual(argv[-2], "--generation")
            self.assertEqual(len(argv[-1]), 32)
            self.assertEqual(argv.count("--generation"), 1)
            self.assertTrue(call.kwargs["start_new_session"])
            self.assertEqual(call.kwargs["user"], 10001)
            self.assertEqual(call.kwargs["group"], 10000)
            self.assertEqual(call.kwargs["extra_groups"], [])
            self.assertEqual(call.kwargs["umask"], 0o002)
        self.assertNotIn("--generation", child.argv)
        python.unlink()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertIsNone(serverboot.xtts_child(self.base, {"voice": {"speak": True}}, self.tree, {}))
        self.assertIn("XTTS", output.getvalue())

    def layout(self, version):
        for rel in updater.REQUIRED:
            path = self.base / rel
            if rel == "tree":
                path.mkdir(exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
        (self.base / "helene-build.json").write_text(json.dumps({"product": updater.PRODUCT, "version": version}), encoding="utf-8")
        (self.base / "app/desk.json").write_text(json.dumps({"version": version}), encoding="utf-8")

    def test_new_server_layout_requires_linux_relay_and_import_closure(self):
        self.layout("1.9.2")
        with self.assertRaises(updater.UpdateError):
            updater.check_layout(self.base, "1.9.2")
        for name in ("server/emergency_stop.py", "server/updater/codecarry.py", "app/localharness/relay_network.py"):
            (self.base / name).write_bytes(b"fixture")
        (self.base / "helene-relay").write_bytes(b"MZwindows")
        with self.assertRaisesRegex(updater.UpdateError, "ELF"):
            updater.check_layout(self.base, "1.9.2")
        (self.base / "helene-relay").write_bytes(b"\x7fELFfixture")
        self.assertTrue(updater.check_layout(self.base, "1.9.2")["relay_linux"])

    def test_old_pristine_remains_readable_for_code_preserving_upgrade(self):
        self.layout("1.9.1")
        (self.base / "helene-relay").unlink()
        self.assertFalse(updater.check_layout(self.base, "1.9.1")["relay_linux"])

    def test_updater_compose_uses_recorded_target_identity(self):
        instance = updater.Updater.__new__(updater.Updater)
        instance.state = {"project": "helene-qualification", "carried": {"HELENE_PORT": "18194"}}
        instance.health = {}
        instance.install = self.base
        instance.cfg = updater.Config(self.base, container="helene-qualification-agent")
        instance.docker = mock.Mock()
        instance.compose("up", "-d", "helene")
        args, kwargs = instance.docker.run.call_args
        self.assertEqual(args[0][:3], ["compose", "-p", "helene-qualification"])
        self.assertEqual(kwargs["env"]["HELENE_CONTAINER"], "helene-qualification-agent")
        self.assertEqual(kwargs["env"]["HELENE_PROJECT"], "helene-qualification")
        self.assertEqual(kwargs["env"]["HELENE_PORT"], "18194")

    def test_xtts_mount_is_carried_without_copying_runtime_into_agent_data(self):
        result = updater.carried({"env": {}, "ports": {}, "mounts": {"/opt/helene-xtts": "/srv/voice/runtime"}})
        self.assertEqual(result, {"HELENE_XTTS_RUNTIME": "/srv/voice/runtime"})

    def test_owner_env_models_and_agent_edits_survive_delivery_replacement(self):
        old = self.base / "old-server"
        new = self.base / "new-server"
        (old / "models").mkdir(parents=True)
        new.mkdir()
        (old / ".env").write_bytes(b"HELENE_PROJECT=isolated\n")
        (old / "models/model.bin").write_bytes(b"owned-model")
        (old / "obsolete.py").write_bytes(b"old-release")
        moved = updater.carry_owner_files(old, new, "server", {"server/obsolete.py"})
        self.assertEqual((new / ".env").read_bytes(), b"HELENE_PROJECT=isolated\n")
        self.assertEqual((new / "models/model.bin").read_bytes(), b"owned-model")
        self.assertFalse((new / "obsolete.py").exists())
        self.assertIn("server/models", moved)
        for name in ("base-code", "mine-code", "new-code"):
            (self.base / name).mkdir()
        (self.base / "base-code/agent.py").write_bytes(b"old-release")
        (self.base / "mine-code/agent.py").write_bytes(b"agent-edit")
        (self.base / "new-code/agent.py").write_bytes(b"old-release")
        report = updater.carry_code(self.base / "base-code", self.base / "mine-code", self.base / "new-code",
                    work=self.base / "merge", labels=("agent", "base", "release"), prefix="tree")
        self.assertEqual((self.base / "new-code/agent.py").read_bytes(), b"agent-edit")
        self.assertTrue(report["carried"])

    def test_compose_and_install_do_not_pin_disposable_instances_to_production(self):
        agent = (DESK / "server/docker-compose.yml").read_text(encoding="utf-8")
        worker = (DESK / "server/updater/docker-compose.yml").read_text(encoding="utf-8")
        script = (DESK / "server/install.sh").read_text(encoding="utf-8")
        self.assertIn("name: ${HELENE_PROJECT:-helene}", agent)
        self.assertIn("container_name: ${HELENE_CONTAINER:-helene}", agent)
        self.assertIn("name: ${HELENE_PROJECT:-helene}-updater", worker)
        self.assertIn("container_name: ${HELENE_CONTAINER:-helene}-updater", worker)
        self.assertNotIn("docker exec helene-updater", script)
        self.assertNotIn("--entrypoint python helene-helene", script)
        self.assertIn('"$PROJECT-helene"', script)
        self.assertIn("COPY helene-relay ./helene-relay", (DESK / "server/Dockerfile").read_text(encoding="utf-8"))

    def test_server_catalog_uses_private_keys_and_endpoint_protocol_without_returning_secrets(self):
        frameworks = {
            "voice": {"protocol": "openai", "base_url": "https://openai.invalid/v2", "api_key": "fixture-openai"},
            "evaluator": {"protocol": "anthropic", "base_url": "https://anthropic.invalid/vendor", "api_key": "fixture-anthropic"},
            "local": {"protocol": "openai", "base_url": "http://127.0.0.1:5011", "api_key": "fixture-loop"},
        }
        path = self.base / "llm.json"
        path.write_text(json.dumps({"frameworks": frameworks}), encoding="utf-8")
        requests = []
        def fetch(request, **kwargs):
            requests.append(request)
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = b'{"data":[{"id":"model"}]}'
            return response
        with mock.patch.object(deskctl, "brain_path", return_value=path), mock.patch("urllib.request.OpenerDirector.open", side_effect=fetch):
            result = deskctl.brain_models()
        self.assertEqual([request.full_url for request in requests],
                         ["https://openai.invalid/v2/models", "https://anthropic.invalid/vendor/v1/models", "http://127.0.0.1:5011/v1/models"])
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer fixture-openai")
        self.assertEqual(requests[1].get_header("X-api-key"), "fixture-anthropic")
        self.assertEqual(requests[1].get_header("Anthropic-version"), "2023-06-01")
        for key in ("fixture-openai", "fixture-anthropic", "fixture-loop"):
            self.assertNotIn(key, json.dumps(result))
        with mock.patch.object(deskctl, "brain_path", return_value=path), mock.patch(
                "urllib.request.OpenerDirector.open", side_effect=OSError("fixture-openai proxy credential echo")):
            errors = deskctl.brain_models()
        self.assertNotIn("fixture-openai", json.dumps(errors))

    def test_catalog_auth_cannot_redirect_to_another_origin(self):
        import urllib.error
        import urllib.request

        path = self.base / "llm.json"
        path.write_text('{"frameworks":{"openai":{"base_url":"https://provider.invalid/v1","api_key":"fixture-key"}}}')
        opener = mock.Mock()
        opener.open.side_effect = OSError("offline fixture")
        with mock.patch.object(deskctl, "brain_path", return_value=path), mock.patch(
                "urllib.request.build_opener", return_value=opener) as build:
            deskctl.brain_models()
        handler = build.call_args.args[0]
        request = urllib.request.Request("https://provider.invalid/v1/models", headers={"Authorization": "Bearer fixture-key"})
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(request, None, 302, "redirect", {}, "https://elsewhere.invalid/models")


@unittest.skipUnless(sys.platform.startswith("linux"), "actual server shell fixture runs on the parent remote Linux lane")
class IsolatedInstall(unittest.TestCase):
    def test_first_install_and_repeat_keep_custom_identity_and_saved_port(self):
        import os
        import shutil
        import subprocess

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            program, target, binaries = root / "program", root / "installed", root / "bin"
            (program / "server/updater").mkdir(parents=True)
            binaries.mkdir()
            shutil.copy(DESK / "server/install.sh", program / "server/install.sh")
            (program / "helene-build.json").write_text(json.dumps({"version": "1.9.2"}, indent=2))
            (program / "server/helene.server.json").write_text('{"tree":"data"}')
            (program / "server/docker-compose.yml").write_text("fixture")
            (program / "server/updater/updater.py").write_text("fixture")
            docker = binaries / "docker"
            docker.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ["TEST_ROOT"])
args = sys.argv[1:]
with (root / "calls").open("a") as output:
    output.write(json.dumps(args) + "\\n")
if args[0] == "inspect":
    if not (root / "running").exists(): sys.exit(1)
    if "-f" in args:
        if "working_dir" in args[2]: print(os.environ["HELENE_DIR"] + "/server")
        else: print("helene-qualification")
elif args[0] == "compose" and "up" in args and not any("updater" in value for value in args):
    (root / "running").write_text("running")
elif args[0] == "logs":
    print("ключ окна\\nfixture-only")
''', encoding="utf-8")
            docker.chmod(0o755)
            env = dict(os.environ, PATH=str(binaries) + ":" + os.environ["PATH"], TEST_ROOT=str(root),
                       HELENE_DIR=str(target), HELENE_PROJECT="helene-qualification",
                       HELENE_CONTAINER="helene-qualification-agent", HELENE_PORT="18194")
            script = ["sh", str(program / "server/install.sh")]
            first = subprocess.run(script, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(first.returncode, 0, first.stderr)
            for name in ("HELENE_PROJECT", "HELENE_CONTAINER", "HELENE_PORT"):
                env.pop(name)
            second = subprocess.run(script, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(second.returncode, 0, second.stderr)
            saved = (target / "server/.env").read_text()
            self.assertIn("HELENE_PROJECT=helene-qualification\n", saved)
            self.assertIn("HELENE_CONTAINER=helene-qualification-agent\n", saved)
            self.assertIn("HELENE_PORT=18194\n", saved)
            calls = [json.loads(row) for row in (root / "calls").read_text().splitlines()]
            selected = [row[1] for row in calls if row[0] == "exec"]
            self.assertTrue(selected)
            self.assertEqual(set(selected), {"helene-qualification-agent-updater"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
