"""Hermetic regressions for scripts.provision_silero (no real network or pip)."""

from __future__ import annotations

import hashlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import provision_silero


class _Response(io.BytesIO):
    def __init__(self, payload: bytes, *, url: str, declared: int | None = None) -> None:
        super().__init__(payload)
        self._url = url
        self.headers = {}
        if declared is not None:
            self.headers["Content-Length"] = str(declared)

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class ProvisionSileroTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "models"
        self.requirements = Path(self.temp.name) / "requirements.txt"
        self.requirements.write_text("example==1\n", encoding="utf-8")
        self.venv_python = self.root / ".venv" / "bin" / "python"
        self.venv_python.parent.mkdir(parents=True)
        self.venv_python.write_bytes(b"#!python\n")
        self.payload = b"verified model bytes"
        self.digest = hashlib.sha256(self.payload).hexdigest()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _argv(self, *extra: str) -> list[str]:
        return [
            "--root", str(self.root),
            "--requirements", str(self.requirements),
            "--model-sha256", self.digest,
            *extra,
        ]

    def test_digest_match_installs_atomically_and_cleans_partial(self) -> None:
        existing = b"old known model"
        model = self.root / provision_silero.MODEL_NAME
        model.write_bytes(existing)

        def download(_url: str, partial: Path) -> None:
            self.assertFalse(partial.exists())
            partial.write_bytes(self.payload)

        with mock.patch.object(provision_silero.subprocess, "run") as run, \
                mock.patch.object(provision_silero, "_download", side_effect=download) as fetch, \
                mock.patch.object(provision_silero.os, "replace", wraps=provision_silero.os.replace) as replace:
            code = provision_silero.main(self._argv())

        self.assertEqual(code, 0)
        self.assertEqual(model.read_bytes(), self.payload)
        self.assertFalse((self.root / ".v5_ru.pt.download").exists())
        fetch.assert_called_once()
        replace.assert_called_once_with(self.root / ".v5_ru.pt.download", model)
        run.assert_called_once_with(
            [str(self.venv_python), "-m", "pip", "install", "-r", str(self.requirements)],
            check=True,
        )

    def test_digest_mismatch_preserves_existing_model_and_cleans_partial(self) -> None:
        existing = b"previous model must survive"
        model = self.root / provision_silero.MODEL_NAME
        model.write_bytes(existing)

        def mismatch(_url: str, partial: Path) -> None:
            partial.write_bytes(b"wrong")

        with mock.patch.object(provision_silero.subprocess, "run"), \
                mock.patch.object(provision_silero, "_download", side_effect=mismatch), \
                mock.patch.object(provision_silero.os, "replace") as replace:
            with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
                provision_silero.main(self._argv())

        self.assertEqual(model.read_bytes(), existing)
        self.assertFalse((self.root / ".v5_ru.pt.download").exists())
        replace.assert_not_called()

    def test_failed_download_and_pip_clean_stale_or_partial_file(self) -> None:
        partial = self.root / ".v5_ru.pt.download"
        partial.write_bytes(b"stale")

        def interrupted(_url: str, destination: Path) -> None:
            destination.write_bytes(b"incomplete")
            raise OSError("offline")

        with mock.patch.object(provision_silero.subprocess, "run"), \
                mock.patch.object(provision_silero, "_download", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "offline"):
                provision_silero.main(self._argv())
        self.assertFalse(partial.exists())

        partial.write_bytes(b"another stale file")
        with mock.patch.object(
            provision_silero.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, ["pip"]),
        ), mock.patch.object(provision_silero, "_download") as download:
            with self.assertRaises(subprocess.CalledProcessError):
                provision_silero.main(self._argv())
        self.assertFalse(partial.exists())
        download.assert_not_called()

    def test_verified_existing_model_skips_download_but_still_verifies_environment(self) -> None:
        model = self.root / provision_silero.MODEL_NAME
        model.write_bytes(self.payload)
        with mock.patch.object(provision_silero.subprocess, "run") as run, \
                mock.patch.object(provision_silero, "_download") as download, \
                mock.patch.object(provision_silero.os, "replace") as replace:
            code = provision_silero.main(self._argv())

        self.assertEqual(code, 0)
        run.assert_called_once()
        download.assert_not_called()
        replace.assert_not_called()
        self.assertEqual(model.read_bytes(), self.payload)

    def test_oversized_existing_model_fails_before_hash_or_provisioning(self) -> None:
        model = self.root / provision_silero.MODEL_NAME
        existing = b"oversized existing model"
        model.write_bytes(existing)
        partial = self.root / f".{provision_silero.MODEL_NAME}.download"
        partial.write_bytes(b"stale partial")

        with mock.patch.object(provision_silero, "MAX_MODEL_BYTES", len(existing) - 1), \
                mock.patch.object(provision_silero, "_sha256") as sha256, \
                mock.patch.object(provision_silero.subprocess, "run") as run, \
                mock.patch.object(provision_silero, "_download") as download, \
                mock.patch.object(provision_silero.shutil, "rmtree") as rmtree, \
                mock.patch.object(provision_silero.os, "replace") as replace:
            with self.assertRaisesRegex(SystemExit, "existing model exceeds.*byte limit"):
                provision_silero.main(self._argv("--force-venv"))

        self.assertEqual(model.read_bytes(), existing)
        self.assertEqual(partial.read_bytes(), b"stale partial")
        sha256.assert_not_called()
        run.assert_not_called()
        download.assert_not_called()
        rmtree.assert_not_called()
        replace.assert_not_called()

    def test_missing_venv_runs_exact_venv_then_pip_subprocesses(self) -> None:
        self.venv_python.unlink()

        def run(command: list[str], *, check: bool) -> mock.Mock:
            self.assertTrue(check)
            if command[:3] == [sys.executable, "-m", "venv"]:
                self.venv_python.parent.mkdir(parents=True, exist_ok=True)
                self.venv_python.write_bytes(b"#!python\n")
            return mock.Mock(returncode=0)

        with mock.patch.object(provision_silero.subprocess, "run", side_effect=run) as runner, \
                mock.patch.object(provision_silero, "_download", side_effect=OSError("stop")):
            with self.assertRaises(OSError):
                provision_silero.main(self._argv())

        self.assertEqual(runner.call_args_list, [
            mock.call([sys.executable, "-m", "venv", str(self.root / ".venv")], check=True),
            mock.call(
                [str(self.venv_python), "-m", "pip", "install", "-r", str(self.requirements)],
                check=True,
            ),
        ])

    def test_force_venv_removes_old_tree_then_recreates_it(self) -> None:
        marker = self.root / ".venv" / "old"
        marker.write_text("old", encoding="utf-8")

        def run(command: list[str], *, check: bool) -> mock.Mock:
            if command[:3] == [sys.executable, "-m", "venv"]:
                self.assertFalse(marker.exists())
                self.venv_python.parent.mkdir(parents=True)
                self.venv_python.write_bytes(b"#!python\n")
            return mock.Mock(returncode=0)

        with mock.patch.object(provision_silero.subprocess, "run", side_effect=run) as runner, \
                mock.patch.object(provision_silero, "_download", side_effect=OSError("stop")):
            with self.assertRaises(OSError):
                provision_silero.main(self._argv("--force-venv"))
        self.assertEqual(len(runner.call_args_list), 2)

    def test_digest_and_paths_fail_before_any_side_effect(self) -> None:
        cases = (
            ["--root", str(self.root), "--requirements", str(self.requirements), "--model-sha256", "no"],
            ["--root", "relative", "--requirements", str(self.requirements), "--model-sha256", self.digest],
            ["--root", "/", "--requirements", str(self.requirements), "--model-sha256", self.digest],
            ["--root", str(self.root), "--requirements", "relative", "--model-sha256", self.digest],
        )
        for argv in cases:
            with self.subTest(argv=argv), mock.patch.object(
                provision_silero.subprocess, "run"
            ) as run, mock.patch.object(provision_silero, "_download") as download:
                with self.assertRaises(SystemExit):
                    provision_silero.main(argv)
                run.assert_not_called()
                download.assert_not_called()

    def test_unsafe_urls_are_rejected_before_subprocess_or_network(self) -> None:
        bad_urls = (
            "http://models.example/model.pt",
            "file:///tmp/model.pt",
            "https://user:pass@models.example/model.pt",
            "https://models.example/model.pt#fragment",
            "https://models.example/model.pt\nnext",
            "https://models.example\\@evil.example/model.pt",
            "https://localhost/model.pt",
            "HTTPS://models.example/model.pt",
            "https:///model.pt",
        )
        for url in bad_urls:
            with self.subTest(url=url), mock.patch.object(
                provision_silero.subprocess, "run"
            ) as run, mock.patch.object(provision_silero.urllib.request, "urlopen") as open_url:
                with self.assertRaises(SystemExit):
                    provision_silero.main(self._argv("--model-url", url))
                run.assert_not_called()
                open_url.assert_not_called()

    def test_download_is_bounded_and_rejects_unsafe_redirect(self) -> None:
        destination = self.root / "download"
        with mock.patch.object(
            provision_silero.urllib.request,
            "urlopen",
            return_value=_Response(b"ok", url="http://unsafe.example/model.pt"),
        ):
            with self.assertRaisesRegex(RuntimeError, "unsafe URL"):
                provision_silero._download(provision_silero.MODEL_URL, destination)
        self.assertFalse(destination.exists())

        with mock.patch.object(provision_silero, "MAX_MODEL_BYTES", 3), \
                mock.patch.object(
                    provision_silero.urllib.request,
                    "urlopen",
                    return_value=_Response(b"four", url=provision_silero.MODEL_URL),
                ):
            with self.assertRaisesRegex(RuntimeError, "download limit"):
                provision_silero._download(provision_silero.MODEL_URL, destination)
        self.assertFalse(destination.exists())

    def test_download_checks_declared_length_and_writes_complete_payload(self) -> None:
        destination = self.root / "download"
        self.root.mkdir(exist_ok=True)
        with mock.patch.object(
            provision_silero.urllib.request,
            "urlopen",
            return_value=_Response(
                self.payload, url=provision_silero.MODEL_URL, declared=len(self.payload)
            ),
        ) as open_url:
            provision_silero._download(provision_silero.MODEL_URL, destination)
        self.assertEqual(destination.read_bytes(), self.payload)
        open_url.assert_called_once_with(
            provision_silero.MODEL_URL,
            timeout=provision_silero.DOWNLOAD_TIMEOUT_SECONDS,
        )


if __name__ == "__main__":
    unittest.main()
