"""Hermetic lifecycle and protocol tests for the isolated Silero worker.

No test imports torch or Silero.  ``FakeModel`` is injected through the worker's
loader seam and writes a small real WAV using only the standard library.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from typing import Any

import silero_tts_worker as worker_module


class FakeModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.final_paths: list[Path] = []

    def save_wav(
        self,
        *,
        text: str,
        speaker: str,
        sample_rate: int,
        audio_path: str,
    ) -> None:
        path = Path(audio_path)
        self.calls.append(
            {
                "text": text,
                "speaker": speaker,
                "sample_rate": sample_rate,
                "audio_path": path,
            }
        )
        # The worker must make the model write a private temporary file, never
        # the parent-reserved final path directly.
        self.asserted_temporary = ".tmp" in path.name
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(sample_rate)
            audio.writeframes(b"\x01\x00" * 240)


class SileroTTSWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.output_root = self.root / "private-output"
        self.output_root.mkdir(mode=0o700)
        self.model_path = self.root / "v5_ru.pt"
        self.model_bytes = b"local verified fake torch package"
        self.model_path.write_bytes(self.model_bytes)
        self.model_sha256 = hashlib.sha256(self.model_bytes).hexdigest()
        self.fake_model = FakeModel()
        self.load_paths: list[Path] = []

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _loader(self, path: Path) -> FakeModel:
        self.load_paths.append(path)
        return self.fake_model

    def _worker(self, **overrides: Any) -> worker_module.SileroTTSWorker:
        values: dict[str, Any] = {
            "model_path": self.model_path,
            "model_sha256": self.model_sha256,
            "output_root": self.output_root,
            "idle_timeout": 1.0,
            "max_requests": 10,
        }
        values.update(overrides)
        return worker_module.SileroTTSWorker(
            worker_module.WorkerConfig(**values), model_loader=self._loader
        )

    def _reserve(self, name: str) -> Path:
        path = self.output_root / name
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        return path

    @staticmethod
    def _request(request_id: str, output: Path, text: str = "Привет, Праксис!") -> dict[str, Any]:
        return {
            "schema": worker_module.REQUEST_SCHEMA,
            "id": request_id,
            "op": "synthesize",
            "text": text,
            "output_path": str(output),
            "model": "v5_ru",
            "speaker": "xenia",
            "sample_rate": 48_000,
        }

    def _serve_records(
        self,
        server: worker_module.SileroTTSWorker,
        records: list[dict[str, Any]],
        *,
        close_input: bool = True,
        wait: float = 2.0,
    ) -> tuple[list[dict[str, Any]], bool]:
        read_fd, write_fd = os.pipe()
        output = io.BytesIO()
        results: list[int] = []
        errors: list[BaseException] = []

        def target() -> None:
            try:
                results.append(
                    worker_module.serve(server, input_fd=read_fd, output=output)
                )
            except BaseException as exc:  # surfaced in the caller thread below
                errors.append(exc)

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        try:
            for record in records:
                payload = json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n"
                os.write(write_fd, payload)
            if close_input:
                os.close(write_fd)
                write_fd = -1
            thread.join(wait)
            finished = not thread.is_alive()
            if errors:
                raise errors[0]
            if finished:
                self.assertEqual(results, [0])
            responses = [
                json.loads(line)
                for line in output.getvalue().decode("utf-8").splitlines()
                if line
            ]
            return responses, finished
        finally:
            if write_fd >= 0:
                os.close(write_fd)
            os.close(read_fd)

    def test_jsonl_protocol_serves_repeated_synthesis_with_lazy_single_load(self) -> None:
        first = self._reserve(".first.part.wav")
        second = self._reserve(".second.part.wav")
        server = self._worker()

        responses, finished = self._serve_records(
            server,
            [
                self._request("request-1", first, "Первая трудная фраза."),
                self._request("request-2", second, "Вторая: 48 кГц."),
            ],
        )

        self.assertTrue(finished)
        self.assertEqual([response["id"] for response in responses], ["request-1", "request-2"])
        self.assertTrue(all(response["ok"] is True for response in responses))
        self.assertEqual(responses[0]["model"], "v5_ru")
        self.assertEqual(responses[0]["speaker"], "xenia")
        self.assertEqual(responses[0]["sample_rate"], 48_000)
        self.assertEqual([response["requests_served"] for response in responses], [1, 2])
        self.assertEqual(len(self.load_paths), 1, "the verified model is loaded only once")
        self.assertEqual(len(self.fake_model.calls), 2)
        self.assertTrue(all(path.stat().st_size > 44 for path in (first, second)))
        self.assertGreaterEqual(responses[-1]["peak_rss_bytes"], responses[-1]["rss_bytes"])

    def test_real_worker_process_exits_cleanly_after_idle_without_loading_torch(self) -> None:
        script = Path(worker_module.__file__).resolve()
        command = [
            sys.executable,
            "-I",
            str(script),
            "--parent-pid",
            str(os.getpid()),
            "--model-path",
            str(self.model_path),
            "--model-sha256",
            self.model_sha256,
            "--output-root",
            str(self.output_root),
            "--idle-timeout",
            "0.1",
            "--max-requests",
            "10",
        ]
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
        )
        try:
            return_code = proc.wait(timeout=3.0)
            self.assertEqual(return_code, 0)
            assert proc.stdout is not None
            assert proc.stderr is not None
            self.assertEqual(proc.stdout.read(), b"")
            self.assertNotIn(b"torch", proc.stderr.read().lower())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()

    def test_max_request_count_marks_recycle_and_exits_with_open_stdin(self) -> None:
        first = self._reserve(".recycle-1.part.wav")
        second = self._reserve(".recycle-2.part.wav")
        server = self._worker(max_requests=2, idle_timeout=10.0)

        started = time.monotonic()
        responses, finished = self._serve_records(
            server,
            [self._request("one", first), self._request("two", second)],
            close_input=False,
            wait=2.0,
        )

        self.assertTrue(finished, "worker must recycle without waiting for pipe EOF")
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(len(responses), 2)
        self.assertFalse(responses[0]["recycle"])
        self.assertTrue(responses[1]["recycle"])
        self.assertEqual(server.requests_served, 2)

    def test_hash_rejection_is_structured_and_prevents_model_load(self) -> None:
        output = self._reserve(".hash-reject.part.wav")
        server = self._worker(model_sha256="0" * 64)

        responses, finished = self._serve_records(
            server, [self._request("wrong-hash", output)]
        )

        self.assertTrue(finished)
        self.assertEqual(len(responses), 1)
        response = responses[0]
        self.assertEqual(response["id"], "wrong-hash")
        self.assertIs(response["ok"], False)
        self.assertEqual(response["error"]["code"], "model_hash_mismatch")
        self.assertIs(response["error"]["retryable"], False)
        self.assertEqual(self.load_paths, [])
        self.assertEqual(output.stat().st_size, 0)

    def test_synthesis_atomically_replaces_reserved_file_with_nonempty_wav(self) -> None:
        output = self._reserve(".atomic.part.wav")
        original_inode = output.stat().st_ino
        server = self._worker()

        response = server.synthesize(self._request("atomic", output))

        self.assertTrue(response["ok"])
        self.assertEqual(response["output_path"], str(output))
        self.assertNotEqual(output.stat().st_ino, original_inode)
        self.assertTrue(self.fake_model.asserted_temporary)
        self.assertEqual(response["bytes"], output.stat().st_size)
        self.assertGreater(output.stat().st_size, 44)
        with wave.open(str(output), "rb") as audio:
            self.assertEqual(audio.getframerate(), 48_000)
            self.assertGreater(audio.getnframes(), 0)
        self.assertEqual(
            sorted(path.name for path in self.output_root.iterdir()),
            [output.name],
            "atomic temporary file must not leak",
        )

    def test_output_path_escape_is_rejected_before_model_load(self) -> None:
        outside = self.root / ".outside.part.wav"
        outside.write_bytes(b"")
        server = self._worker()

        with self.assertRaises(worker_module.WorkerFailure) as caught:
            server.synthesize(self._request("escape", outside))

        self.assertEqual(caught.exception.code, "unsafe_output")
        self.assertEqual(self.load_paths, [])
        self.assertEqual(outside.read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
