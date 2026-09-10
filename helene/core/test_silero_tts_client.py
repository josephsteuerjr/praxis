"""Hermetic lifecycle tests for the parent-side Silero subprocess supervisor."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import sys as _port_sys


def setUpModule():  # порт: named-skip всего модуля на винде
    if _port_sys.platform == "win32":
        raise unittest.SkipTest("порт: голосовой воркер (silero/torch, POSIX-процессы) не входит в v1 винды — шаг «голос» отдельно")
import wave
from pathlib import Path

import silero_tts_client


_FAKE_WORKER = r'''#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import time
import wave
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--parent-pid", required=True)
parser.add_argument("--model-path", required=True)
parser.add_argument("--model-sha256", required=True)
parser.add_argument("--output-root", required=True)
parser.add_argument("--idle-timeout", required=True)
parser.add_argument("--max-requests", required=True)
parser.add_argument("--max-text-chars", required=True)
parser.add_argument("--max-request-bytes", required=True)
parser.add_argument("--max-output-bytes", required=True)
args = parser.parse_args()
root = Path(args.output_root).resolve()

for raw in sys.stdin.buffer:
    request = json.loads(raw.decode("utf-8"))
    text = request["text"]
    request_id = request["id"]
    if text == "timeout":
        time.sleep(30)
    if text == "crash":
        os._exit(23)
    if text == "malformed":
        sys.stdout.write("this is not json\n")
        sys.stdout.flush()
        continue
    if text == "wrong-id":
        print(json.dumps({"schema": "praxis.silero.tts.response.v1",
                          "id": "other", "ok": True,
                          "output_path": request["output_path"], "bytes": 100,
                          "model": request["model"],
                          "speaker": request["speaker"],
                          "sample_rate": request["sample_rate"],
                          "rss_bytes": 1024, "peak_rss_bytes": 2048}),
              flush=True)
        continue

    response_override = {}
    if text == "wrong-schema":
        response_override["schema"] = "wrong"
    elif text == "missing-bytes":
        response_override["drop"] = "bytes"
    elif text == "wrong-bytes":
        response_override["bytes"] = 1
    elif text == "wrong-model":
        response_override["model"] = "other"
    elif text == "wrong-speaker":
        response_override["speaker"] = "other"
    elif text == "wrong-rate":
        response_override["sample_rate"] = 24000
    elif text == "bad-rss":
        response_override["rss_bytes"] = True
    elif text == "bad-peak":
        response_override["peak_rss_bytes"] = 1
    elif text == "bad-path-type":
        response_override["output_path"] = 123
    if text == "orphan":
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        (root / "grandchild.pid").write_text(str(child.pid), encoding="ascii")
        time.sleep(30)

    output = Path(request["output_path"])
    # The real worker enforces this too. It also makes the fake a useful path-
    # contract check rather than blindly writing wherever the client asks.
    if output.parent.resolve() != root:
        print(json.dumps({"schema": "praxis.silero.tts.response.v1",
                          "id": request_id, "ok": False,
                          "error": {"code": "unsafe_path", "message": "bad"}}),
              flush=True)
        continue
    with wave.open(str(output), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(int(request["sample_rate"]))
        wav_file.writeframes(b"\x00\x00" * 64)
    if text == "wrong-path":
        returned_path = str(root / "untrusted.wav")
    else:
        returned_path = str(output)
    response = {"schema": "praxis.silero.tts.response.v1",
                "id": request_id, "ok": True,
                "output_path": returned_path,
                "bytes": output.stat().st_size,
                "model": request["model"],
                "speaker": request["speaker"],
                "sample_rate": request["sample_rate"],
                "rss_bytes": 1024,
                "peak_rss_bytes": 2048}
    drop = response_override.pop("drop", None)
    response.update(response_override)
    if drop:
        response.pop(drop, None)
    print(json.dumps(response), flush=True)
'''


def _pid_state(pid: int) -> str | None:
    """Return the Linux proc state, treating an already-reaped pid as absent."""

    try:
        fields = (Path("/proc") / str(pid) / "stat").read_text(
            encoding="ascii"
        ).split()
    except (FileNotFoundError, OSError):
        return None
    return fields[2] if len(fields) >= 3 else None


class SileroTTSClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output_dir = self.root / "out"
        self.worker = self.root / "fake_worker.py"
        self.worker.write_text(_FAKE_WORKER, encoding="utf-8")
        self.model = self.root / "model.pt"
        self.model.write_bytes(b"not-a-real-model")
        self.config = silero_tts_client.SileroTTSConfig(
            executable=sys.executable,
            worker_path=self.worker,
            model_path=self.model,
            model_sha256="a" * 64,
            output_dir=self.output_dir,
            request_timeout_seconds=0.35,
            idle_timeout_seconds=10.0,
            terminate_grace_seconds=0.1,
            rss_poll_interval_seconds=0.01,
            max_rss_bytes=256 * 1024 * 1024,
            recycle_after_requests=100,
            max_chars=100,
        )
        self.clients: list[silero_tts_client.SileroTTSClient] = []

    def tearDown(self) -> None:
        for client in self.clients:
            client.clear_cache()
        self.temp.cleanup()

    def make_client(self, **kwargs: object) -> silero_tts_client.SileroTTSClient:
        client = silero_tts_client.SileroTTSClient(self.config, **kwargs)
        self.clients.append(client)
        return client

    def assert_no_partials(self) -> None:
        if self.output_dir.exists():
            self.assertEqual(list(self.output_dir.glob("*.part.wav")), [])
            self.assertEqual(list(self.output_dir.glob(".*.part.wav")), [])

    def test_lazy_start_reuses_worker_and_writes_unique_atomic_wav(self) -> None:
        client = self.make_client()
        initial = client.status()
        self.assertIsNone(initial["pid"])
        self.assertEqual(initial["starts"], 0)
        self.assertFalse(self.output_dir.exists())

        first = client.synthesize("  Привет  ")
        first_pid = client.pid
        second = client.synthesize("Ещё раз")

        self.assertIsNotNone(first_pid)
        self.assertEqual(client.pid, first_pid)
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, self.output_dir.resolve())
        self.assertFalse(first.name.startswith("."))
        self.assertEqual(first.stat().st_mode & 0o777, 0o600)
        for artifact in (first, second):
            with wave.open(str(artifact), "rb") as audio:
                self.assertGreater(audio.getnframes(), 0)
                self.assertEqual(audio.getframerate(), 48_000)

        status = client.status()
        self.assertEqual(status["starts"], 1)
        self.assertEqual(status["requests"], 2)
        self.assertEqual(status["process_requests"], 2)
        self.assertGreater(status["peak_rss_bytes"], 0)
        self.assertEqual(status["failures"], 0)
        self.assert_no_partials()

    def test_requests_are_serialized_across_calling_threads(self) -> None:
        client = self.make_client()
        barrier = threading.Barrier(3)
        paths: list[Path] = []
        errors: list[BaseException] = []

        def call(text: str) -> None:
            barrier.wait()
            try:
                paths.append(client.synthesize(text))
            except BaseException as exc:  # captured and asserted in the test thread
                errors.append(exc)

        threads = [
            threading.Thread(target=call, args=("один",)),
            threading.Thread(target=call, args=("два",)),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=3)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(paths), 2)
        self.assertEqual(client.status()["starts"], 1)
        self.assertEqual(client.status()["requests"], 2)

    def test_timeout_kills_and_reaps_before_reporting_failure(self) -> None:
        client = self.make_client()
        before = time.monotonic()
        with self.assertRaises(silero_tts_client.SileroTTSTimeoutError):
            client.synthesize("timeout")
        elapsed = time.monotonic() - before

        self.assertLess(elapsed, 2.0)
        status = client.status()
        self.assertIsNone(status["pid"])
        self.assertEqual(status["failures"], 1)
        self.assertEqual(status["recycles"], 1)
        self.assertEqual(status["last_error"], "timeout")
        self.assert_no_partials()

        restarted = client.synthesize("после таймаута")
        self.assertTrue(restarted.is_file())
        self.assertEqual(client.status()["starts"], 2)

    def test_crash_and_malformed_reply_poison_worker_then_restart(self) -> None:
        client = self.make_client()
        for text in ("crash", "malformed", "wrong-id", "wrong-path"):
            with self.subTest(text=text):
                with self.assertRaises(silero_tts_client.SileroTTSProcessError):
                    client.synthesize(text)
                self.assertIsNone(client.pid)
                self.assert_no_partials()
                artifact = client.synthesize(f"recovered-{text}")
                self.assertTrue(artifact.is_file())

        status = client.status()
        self.assertEqual(status["failures"], 4)
        self.assertEqual(status["requests"], 4)
        self.assertEqual(status["starts"], 5)

    def test_rss_guard_seam_kills_worker_and_records_observed_peak(self) -> None:
        samples = iter(
            [1024, self.config.max_rss_bytes + 1, self.config.max_rss_bytes + 1]
        )

        def high_rss(_pid: int) -> int:
            return next(samples, self.config.max_rss_bytes + 1)

        client = self.make_client(rss_reader=high_rss)
        with self.assertRaises(silero_tts_client.SileroTTSMemoryError):
            client.synthesize("память")

        status = client.status()
        self.assertIsNone(status["pid"])
        self.assertEqual(status["failures"], 1)
        self.assertEqual(status["last_error"], "rss_limit")
        self.assertGreater(status["peak_rss_bytes"], self.config.max_rss_bytes)
        self.assert_no_partials()

    def test_clear_cache_reaps_child_and_next_request_restarts(self) -> None:
        client = self.make_client()
        client.synthesize("первый")
        first_pid = client.pid
        self.assertIsNotNone(first_pid)

        client.clear_cache()
        self.assertIsNone(client.pid)
        if os.name == "posix" and first_pid is not None:
            self.assertIsNone(_pid_state(first_pid), "worker leader was not reaped")

        client.synthesize("второй")
        self.assertIsNotNone(client.pid)
        status = client.status()
        self.assertEqual(status["starts"], 2)
        self.assertEqual(status["recycles"], 1)
        self.assertEqual(status["failures"], 0)

    def test_request_limit_recycles_after_success_and_restarts_lazily(self) -> None:
        config = silero_tts_client.SileroTTSConfig(
            **{**self.config.__dict__, "recycle_after_requests": 2}
        )
        client = silero_tts_client.SileroTTSClient(config)
        self.clients.append(client)

        client.synthesize("раз")
        pid = client.pid
        client.synthesize("два")
        self.assertIsNone(client.pid)
        self.assertEqual(client.status()["recycles"], 1)
        if os.name == "posix" and pid is not None:
            self.assertIsNone(_pid_state(pid))

        client.synthesize("три")
        self.assertIsNotNone(client.pid)
        self.assertEqual(client.status()["starts"], 2)

    @unittest.skipUnless(os.name == "posix", "process-group lifecycle is POSIX-only")
    def test_timeout_kills_spawned_descendant_not_leaves_it_running(self) -> None:
        """Prove group signalling, not reaping of a non-child descendant.

        The client can reap only its direct worker.  A descendant becomes PID
        1's responsibility after its parent exits; accepting ``Z`` here is an
        honest assertion that no executable descendant remains, not a claim
        that this process architecture can reap someone else's child.
        """

        client = self.make_client()
        with self.assertRaises(silero_tts_client.SileroTTSTimeoutError):
            client.synthesize("orphan")

        pid_file = self.output_dir / "grandchild.pid"
        self.assertTrue(pid_file.is_file())
        grandchild_pid = int(pid_file.read_text(encoding="ascii"))
        deadline = time.monotonic() + 2.0
        state = _pid_state(grandchild_pid)
        while state not in (None, "Z") and time.monotonic() < deadline:
            time.sleep(0.01)
            state = _pid_state(grandchild_pid)
        self.assertIn(
            state,
            (None, "Z"),
            "a descendant remained executable after whole-group termination",
        )
        self.assertIsNone(client.pid)

    def test_configuration_and_input_fail_before_process_start(self) -> None:
        with self.assertRaises(silero_tts_client.SileroTTSConfigurationError):
            silero_tts_client.SileroTTSConfig(
                **{**self.config.__dict__, "model_sha256": "not-a-digest"}
            )

        client = self.make_client()
        for value in ("", "   ", 123):
            with self.subTest(value=value):
                with self.assertRaises(
                    silero_tts_client.SileroTTSConfigurationError
                ):
                    client.synthesize(value)  # type: ignore[arg-type]
        with self.assertRaises(silero_tts_client.SileroTTSConfigurationError):
            client.synthesize("x" * (self.config.max_chars + 1))
        self.assertEqual(client.status()["starts"], 0)

    def test_direct_config_rejects_noncanonical_types_and_ranges(self) -> None:
        invalid = {
            "worker_path": str(self.worker),
            "model_path": str(self.model),
            "output_dir": str(self.output_dir),
            "model_sha256": 123,
            "executable": Path(sys.executable),
            "request_timeout_seconds": "0.3",
            "idle_timeout_seconds": float("nan"),
            "terminate_grace_seconds": float("inf"),
            "rss_poll_interval_seconds": True,
            "max_rss_bytes": True,
            "recycle_after_requests": 1.0,
            "max_chars": "2",
            "max_request_bytes": silero_tts_client.DEFAULT_MAX_REQUEST_BYTES + 1,
            "max_output_bytes": False,
            "model": 123,
            "speaker": 123,
            "sample_rate": True,
        }
        for name, value in invalid.items():
            with self.subTest(name=name, value=value):
                with self.assertRaises(silero_tts_client.SileroTTSConfigurationError):
                    silero_tts_client.SileroTTSConfig(
                        **{**self.config.__dict__, name: value}
                    )
        self.assertFalse(self.output_dir.exists())

    def test_request_utf8_byte_limit_fails_before_start_and_cleans_reservations(self) -> None:
        config = silero_tts_client.SileroTTSConfig(
            **{**self.config.__dict__, "max_request_bytes": 260}
        )
        client = silero_tts_client.SileroTTSClient(config)
        self.clients.append(client)

        with self.assertRaises(silero_tts_client.SileroTTSConfigurationError):
            client.synthesize("я" * 100)
        self.assertEqual(client.status()["starts"], 0)
        self.assertIsNone(client.pid)
        self.assertEqual(list(self.output_dir.iterdir()), [])

    def test_lock_wait_is_part_of_end_to_end_deadline(self) -> None:
        client = self.make_client()
        client._lock.acquire()
        errors: list[BaseException] = []
        started = time.monotonic()
        thread = threading.Thread(
            target=lambda: self._capture_synthesis_error(client, "очередь", errors)
        )
        thread.start()
        time.sleep(self.config.request_timeout_seconds + 0.1)
        client._lock.release()
        thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], silero_tts_client.SileroTTSTimeoutError)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(client.status()["starts"], 0)
        self.assertFalse(self.output_dir.exists())

    @staticmethod
    def _capture_synthesis_error(
        client: silero_tts_client.SileroTTSClient,
        text: str,
        errors: list[BaseException],
    ) -> None:
        try:
            client.synthesize(text)
        except BaseException as exc:
            errors.append(exc)

    def test_popen_returned_after_deadline_is_killed_and_reaped(self) -> None:
        returned = threading.Event()
        process_holder: list[subprocess.Popen[bytes]] = []

        def slow_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
            time.sleep(self.config.request_timeout_seconds + 0.2)
            proc = subprocess.Popen(*args, **kwargs)  # type: ignore[arg-type]
            process_holder.append(proc)
            returned.set()
            return proc

        client = self.make_client(popen_factory=slow_popen)
        before = time.monotonic()
        with self.assertRaises(silero_tts_client.SileroTTSTimeoutError):
            client.synthesize("медленный запуск")
        self.assertLess(time.monotonic() - before, 0.7)
        self.assertIsNone(client.pid)
        self.assertEqual(list(self.output_dir.iterdir()), [])

        self.assertTrue(returned.wait(2), "injected Popen never returned")
        proc = process_holder[0]
        deadline = time.monotonic() + 2
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(proc.poll(), "late child was left alive")
        if os.name == "posix":
            self.assertIsNone(_pid_state(proc.pid), "late child leader was not reaped")

    def test_late_started_child_blocks_fallback_until_cleanup_finishes(self) -> None:
        created = threading.Event()
        release_handle = threading.Event()
        process_holder: list[subprocess.Popen[bytes]] = []

        def withheld_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
            proc = subprocess.Popen(*args, **kwargs)  # type: ignore[arg-type]
            process_holder.append(proc)
            created.set()
            if not release_handle.wait(2):
                raise AssertionError("test did not release the late Popen handle")
            return proc

        client = self.make_client(popen_factory=withheld_popen)
        with self.assertRaises(silero_tts_client.SileroTTSTimeoutError):
            client.synthesize("неопределённый запуск")

        self.assertTrue(created.is_set())
        self.assertFalse(
            client.fallback_ready(),
            "fallback became eligible while an unpublished child could still be alive",
        )
        release_handle.set()
        deadline = time.monotonic() + 2
        while not client.fallback_ready() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(client.fallback_ready())
        proc = process_holder[0]
        self.assertIsNotNone(proc.poll(), "late child was not contained before fallback")
        if os.name == "posix":
            self.assertIsNone(_pid_state(proc.pid), "late child leader was not reaped")

    def test_success_response_contract_is_strict_and_preserves_rss_evidence(self) -> None:
        client = self.make_client()
        invalid = (
            "wrong-schema",
            "missing-bytes",
            "wrong-bytes",
            "wrong-model",
            "wrong-speaker",
            "wrong-rate",
            "bad-rss",
            "bad-peak",
            "bad-path-type",
        )
        for text in invalid:
            with self.subTest(text=text):
                with self.assertRaises(silero_tts_client.SileroTTSProcessError):
                    client.synthesize(text)
                self.assertIsNone(client.pid)
                self.assertEqual(list(self.output_dir.iterdir()), [])

        artifact = client.synthesize("доказательство rss")
        self.assertTrue(artifact.is_file())
        observed = client.status()["rss_bytes"]
        self.assertEqual(observed, 1024)
        client.clear_cache()
        status = client.status()
        self.assertFalse(status["alive"])
        self.assertEqual(status["rss_bytes"], observed)

    def test_startup_sweep_deletes_only_proven_stale_owned_crash_artifacts(self) -> None:
        self.output_dir.mkdir()
        stale_pid = 999_999_999
        identity = f"{stale_pid}-1-{'a' * 32}-{'b' * 32}"
        partial = self.output_dir / f".tts-silero-{identity}.part.wav"
        final = self.output_dir / f"tts-silero-{identity}.wav"
        temporary = self.output_dir / (
            f"..tts-silero-{identity}.part.wav.{'c' * 32}.tmp"
        )
        unrelated = self.output_dir / ".tts-silero-user.part.wav"
        completed = self.output_dir / f"tts-silero-{identity[:-1]}c.wav"
        for path in (partial, final, temporary):
            path.write_bytes(b"")
        unrelated.write_bytes(b"keep")
        completed.write_bytes(b"completed")

        client = self.make_client()
        artifact = client.synthesize("очистка")

        self.assertTrue(artifact.is_file())
        for path in (partial, final, temporary):
            self.assertFalse(path.exists())
        self.assertEqual(unrelated.read_bytes(), b"keep")
        self.assertEqual(completed.read_bytes(), b"completed")

    def test_real_worker_protocol_with_fake_torch_handles_post_response_exit(self) -> None:
        fake_root = self.root / "fake-site"
        torch_package = fake_root / "torch"
        torch_package.mkdir(parents=True)
        (torch_package / "__init__.py").write_text(
            "from . import package\n", encoding="utf-8"
        )
        (torch_package / "package.py").write_text(
            """import wave
class Model:
    def save_wav(self, *, text, speaker, sample_rate, audio_path):
        with wave.open(audio_path, 'wb') as output:
            output.setnchannels(1); output.setsampwidth(2)
            output.setframerate(sample_rate); output.writeframes(b'\\0\\0' * 32)
class PackageImporter:
    def __init__(self, path): self.path = path
    def load_pickle(self, package, resource): return Model()
""",
            encoding="utf-8",
        )
        launcher = self.root / "fake-python"
        launcher.write_text(
            "#!" + sys.executable + "\n"
            + "import runpy,sys\n"
            + "sys.path.insert(0, " + repr(str(fake_root)) + ")\n"
            + "sys.argv=sys.argv[2:]\n"
            + "runpy.run_path(sys.argv[0],run_name='__main__')\n",
            encoding="utf-8",
        )
        launcher.chmod(0o700)
        worker = Path(silero_tts_client.__file__).with_name("silero_tts_worker.py")
        digest = hashlib.sha256(self.model.read_bytes()).hexdigest()
        config = silero_tts_client.SileroTTSConfig(
            **{
                **self.config.__dict__,
                "worker_path": worker,
                "executable": str(launcher),
                "model_sha256": digest,
                "recycle_after_requests": 1,
                # This test proves the real worker protocol/recycle boundary, not
                # the soft RSS rail.  Full-suite instrumentation can lift a fresh
                # interpreter above the small fixture's 256 MiB ceiling.
                "max_rss_bytes": 2 * 1024 * 1024 * 1024,
                "request_timeout_seconds": 2.0,
            }
        )
        client = silero_tts_client.SileroTTSClient(config)
        self.clients.append(client)

        artifact = client.synthesize("точный протокол")

        self.assertTrue(artifact.is_file())
        self.assertIsNone(client.pid)
        status = client.status()
        self.assertEqual(status["requests"], 1)
        self.assertGreater(status["rss_bytes"], 0)
        self.assertGreaterEqual(status["peak_rss_bytes"], status["rss_bytes"])

    def test_from_env_exposes_concrete_media_audio_configuration_api(self) -> None:
        config = silero_tts_client.SileroTTSConfig.from_env(
            output_dir=self.output_dir,
            environ={
                "PRAXIS_SILERO_PYTHON": sys.executable,
                "PRAXIS_SILERO_WORKER": str(self.worker),
                "PRAXIS_SILERO_MODEL": str(self.model),
                "PRAXIS_SILERO_MODEL_SHA256": "B" * 64,
                "PRAXIS_SILERO_TIMEOUT": "12.5",
                "PRAXIS_SILERO_MAX_RSS_BYTES": "123456",
                "PRAXIS_SILERO_RECYCLE_REQUESTS": "7",
            },
        )
        self.assertEqual(config.worker_path, self.worker)
        self.assertEqual(config.model_path, self.model)
        self.assertEqual(config.model_sha256, "b" * 64)
        self.assertEqual(config.output_dir, self.output_dir)
        self.assertEqual(config.request_timeout_seconds, 12.5)
        self.assertEqual(config.max_rss_bytes, 123456)
        self.assertEqual(config.recycle_after_requests, 7)
        self.assertIs(silero_tts_client.SileroTTS, silero_tts_client.SileroTTSClient)


if __name__ == "__main__":
    unittest.main()
