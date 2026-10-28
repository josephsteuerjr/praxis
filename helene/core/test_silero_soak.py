"""Hermetic tests for the bounded, fail-closed isolated-Silero soak gate."""

from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts import silero_soak


_REQUIRED_RAILS = (
    "--max-worker-rss-bytes", "4096",
    "--max-cgroup-current-bytes", "8192",
    "--max-cgroup-swap-bytes", "1",
    "--max-psi-some-avg10", "0.1",
)


class _StepClock:
    def __init__(self, step: float = 0.1) -> None:
        self.value = -step
        self.step = step

    def __call__(self) -> float:
        self.value += self.step
        return self.value


class _FakeClient:
    def __init__(self, config: object, *, fail: bool = False) -> None:
        self.config = config
        self.fail = fail
        self._pid: int | None = None
        self.requests = 0
        self.cleared = False

    @property
    def pid(self) -> int | None:
        return self._pid

    def synthesize(self, _text: str) -> Path:
        self.requests += 1
        if self.fail:
            raise RuntimeError("synthetic failure " + "x" * 500)
        self._pid = 12345
        path = Path(self.config.output_dir) / f"fake-{self.requests}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(48_000)
            audio.writeframes(b"\x00\x00" * 480)
        # Idle exit is a worker property.  Make it immediately observable here.
        self._pid = None
        return path

    def status(self) -> dict[str, object]:
        return {
            "pid": self._pid,
            "alive": self._pid is not None,
            "rss_bytes": 0 if self._pid is None else 1000,
            "peak_rss_bytes": 2000,
            "starts": int(self.requests > 0),
            "requests": self.requests,
            "process_requests": 0 if self._pid is None else self.requests,
            "recycles": 0,
            "failures": self.requests if self.fail else 0,
            "last_error": "processing" if self.fail else None,
        }

    def clear_cache(self) -> None:
        self.cleared = True
        self._pid = None


class SileroSoakTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output = self.root / "audio"
        self.report = self.root / "report.json"
        self.config = SimpleNamespace(
            output_dir=self.output,
            idle_timeout_seconds=0.01,
            terminate_grace_seconds=0.01,
            request_timeout_seconds=1.0,
            model="v5_ru",
            model_sha256="a" * 64,
            speaker="xenia",
            sample_rate=48_000,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _run(
        self,
        client: _FakeClient,
        *extra: str,
        clock: object | None = None,
        cgroup: dict[str, int | float | None] | None = None,
    ) -> tuple[int, dict[str, object]]:
        argv = [
            "--duration-minutes", "0.01",
            "--interval-seconds", "0",
            "--idle-wait-seconds", "0.01",
            "--report", str(self.report),
            *_REQUIRED_RAILS,
            *extra,
        ]
        if cgroup is None:
            cgroup = {
                "current_bytes": 4096,
                "swap_bytes": 0,
                "psi_some_avg10": 0.0,
            }
        if clock is None:
            clock = _StepClock()
        with mock.patch.object(
            silero_soak.SileroTTSConfig, "from_env", return_value=self.config
        ), mock.patch.object(
            silero_soak, "SileroTTSClient", return_value=client
        ), mock.patch.object(
            silero_soak, "_sample_cgroup", return_value=cgroup
        ), mock.patch.object(
            silero_soak.time, "monotonic", side_effect=clock
        ):
            code = silero_soak.main(argv, _allow_short_duration=True)
        return code, json.loads(self.report.read_text(encoding="utf-8"))

    def test_green_gate_writes_atomic_machine_readable_bounded_report(self) -> None:
        client = _FakeClient(self.config)
        code, report = self._run(client)

        self.assertEqual(code, 0)
        self.assertTrue(report["passed"])
        self.assertEqual(report["schema"], "praxis.silero.soak.v2")
        self.assertGreaterEqual(report["successful_requests"], 1)
        self.assertTrue(report["idle_unloaded"])
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(report["limits"]["max_report_bytes"], silero_soak.MAX_REPORT_BYTES)
        self.assertLessEqual(self.report.stat().st_size, silero_soak.MAX_REPORT_BYTES)
        self.assertEqual(report["model_sha256"], "a" * 64)
        self.assertTrue(client.cleared)
        self.assertEqual(list(self.output.glob("*.wav")), [])
        self.assertFalse(self.report.with_name(f".{self.report.name}.part").exists())

    def test_real_cli_accepts_only_thirty_to_sixty_minutes(self) -> None:
        required = ["--report", str(self.report), *_REQUIRED_RAILS]
        for duration in ("0.001", "29.999", "61", "1000000000", "nan", "inf"):
            with self.subTest(duration=duration), self.assertRaises(SystemExit):
                silero_soak.main(["--duration-minutes", duration, *required])
        # Parsing the inclusive endpoints does not instantiate a client.
        for duration in ("30", "60"):
            with self.subTest(duration=duration):
                args = silero_soak._parser().parse_args(
                    ["--duration-minutes", duration, *required]
                )
                self.assertEqual(args.duration_minutes, float(duration))

    def test_threshold_omission_is_rejected_before_client_construction(self) -> None:
        complete = ["--report", str(self.report), *_REQUIRED_RAILS]
        for option in _REQUIRED_RAILS[::2]:
            argv = complete.copy()
            index = argv.index(option)
            del argv[index:index + 2]
            with self.subTest(option=option), mock.patch.object(
                silero_soak, "SileroTTSClient"
            ) as client, self.assertRaises(SystemExit):
                silero_soak.main(argv)
            client.assert_not_called()

    def test_all_fail_cannot_pass_even_with_failure_budget(self) -> None:
        client = _FakeClient(self.config, fail=True)
        code, report = self._run(
            client,
            "--max-failures", "1000",
            "--max-requests", "5",
            clock=lambda: 0.0,
        )

        self.assertEqual(code, 1)
        self.assertFalse(report["passed"])
        self.assertEqual(report["successful_requests"], 0)
        self.assertFalse(report["checks"]["successful_synthesis"])
        self.assertEqual(report["requests"], 5)
        self.assertIn("request_limit_reached_before_duration", report["stop_reasons"])
        self.assertTrue(client.cleared)

    def test_fast_failures_requests_samples_details_and_report_are_bounded(self) -> None:
        client = _FakeClient(self.config, fail=True)
        code, report = self._run(
            client,
            "--max-failures", "10000",
            "--max-requests", "300",
            clock=lambda: 0.0,
        )

        self.assertEqual(code, 1)
        self.assertEqual(report["requests"], 300)
        self.assertEqual(report["failure_count"], 300)
        self.assertLessEqual(len(report["failures"]), silero_soak.MAX_FAILURE_DETAILS)
        self.assertLessEqual(len(report["samples"]), silero_soak.MAX_SAMPLE_RECORDS)
        self.assertGreater(report["evidence"]["failure_details_omitted"], 0)
        self.assertGreater(report["evidence"]["sample_records_omitted"], 0)
        self.assertLessEqual(self.report.stat().st_size, silero_soak.MAX_REPORT_BYTES)

    def test_exceeded_failure_and_resource_rails_stop_immediately(self) -> None:
        client = _FakeClient(self.config, fail=True)
        code, report = self._run(client, "--max-failures", "0", clock=lambda: 0.0)
        self.assertEqual(code, 1)
        self.assertEqual(report["requests"], 1)
        self.assertIn("failure_budget_exceeded", report["stop_reasons"])

        self.report.unlink()
        client = _FakeClient(self.config)
        code, report = self._run(
            client,
            clock=lambda: 0.0,
            cgroup={"current_bytes": 9999, "swap_bytes": 0, "psi_some_avg10": 0.0},
        )
        self.assertEqual(code, 1)
        self.assertEqual(report["requests"], 1)
        self.assertIn("cgroup_current_limit_exceeded", report["stop_reasons"])

    def test_unavailable_mandatory_metric_fails_closed(self) -> None:
        client = _FakeClient(self.config)
        code, report = self._run(
            client,
            clock=lambda: 0.0,
            cgroup={"current_bytes": None, "swap_bytes": 0, "psi_some_avg10": 0.0},
        )

        self.assertEqual(code, 1)
        self.assertFalse(report["checks"]["cgroup_current_within_limit"])
        self.assertIn("cgroup_current_unavailable", report["stop_reasons"])

    def test_keep_audio_is_bounded_and_excess_files_are_deleted(self) -> None:
        client = _FakeClient(self.config)
        code, report = self._run(
            client,
            "--keep-audio",
            "--max-kept-audio", "2",
            "--max-requests", "6",
            clock=lambda: 0.0,
        )

        # The request bound intentionally makes this synthetic run red, but retained
        # evidence remains useful and cannot consume unbounded disk.
        self.assertEqual(code, 1)
        self.assertEqual(report["requests"], 6)
        self.assertEqual(len(report["artifacts"]), 2)
        self.assertEqual(len(list(self.output.glob("*.wav"))), 2)
        self.assertEqual(report["evidence"]["audio_artifacts_not_kept"], 4)
        for artifact in report["artifacts"]:
            self.assertTrue((self.output / artifact["name"]).is_file())
            self.assertGreater(artifact["bytes"], 0)
            self.assertEqual(len(artifact["sha256"]), 64)

    def test_keep_audio_is_also_bounded_by_aggregate_bytes(self) -> None:
        client = _FakeClient(self.config)
        code, report = self._run(
            client,
            "--keep-audio",
            "--max-kept-audio", "10",
            "--max-kept-audio-bytes", "1100",
            "--max-requests", "3",
            clock=lambda: 0.0,
        )

        self.assertEqual(code, 1)
        self.assertEqual(len(report["artifacts"]), 1)
        self.assertEqual(len(list(self.output.glob("*.wav"))), 1)
        self.assertEqual(report["limits"]["max_kept_audio_bytes"], 1100)
        self.assertLessEqual(report["evidence"]["retained_audio_bytes"], 1100)
        self.assertEqual(
            report["evidence"]["retained_audio_bytes"],
            report["artifacts"][0]["bytes"],
        )
        self.assertEqual(report["evidence"]["audio_artifacts_not_kept"], 2)

    def test_parser_caps_request_artifact_and_idle_wait_limits(self) -> None:
        base = ["--report", str(self.report), *_REQUIRED_RAILS]
        for option, value in (
            ("--max-requests", str(silero_soak.MAX_REQUESTS + 1)),
            ("--max-kept-audio", str(silero_soak.MAX_KEPT_AUDIO + 1)),
            (
                "--max-kept-audio-bytes",
                str(silero_soak.MAX_KEPT_AUDIO_BYTES + 1),
            ),
            ("--idle-wait-seconds", str(silero_soak.MAX_IDLE_WAIT_SECONDS + 1)),
        ):
            with self.subTest(option=option), self.assertRaises(SystemExit):
                silero_soak._parser().parse_args([*base, option, value])

    def test_corpus_and_client_request_timeout_are_bounded(self) -> None:
        corpus = self.root / "corpus.txt"
        corpus.write_text("x" * (silero_soak.MAX_CORPUS_LINE_CHARS + 1), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "corpus line"):
            silero_soak._load_corpus(corpus)

        self.config.request_timeout_seconds = silero_soak.MAX_REQUEST_TIMEOUT_SECONDS + 1
        with mock.patch.object(
            silero_soak.SileroTTSConfig, "from_env", return_value=self.config
        ), mock.patch.object(silero_soak, "SileroTTSClient") as client:
            with self.assertRaisesRegex(SystemExit, "request timeout"):
                silero_soak.main([
                    "--duration-minutes", "30",
                    "--report", str(self.report),
                    *_REQUIRED_RAILS,
                ])
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
