#!/usr/bin/env python3
"""Provision the immutable local Silero worker environment and model.

Nothing here changes ``PRAXIS_TTS_BACKEND`` or restarts Praxis.  The command is an
explicit provisioning step before the soak gate.  It verifies the downloaded model
against the accepted digest and atomically replaces the deployment artifact only
after verification.  Downloads and temporary files are bounded and fail-clean.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import BinaryIO

MODEL_URL = "https://models.silero.ai/models/tts/ru/v5_ru.pt"
MODEL_SHA256 = "7ba04d42340fe0398042eed2e0d12d62e23096d626b1b9feff4dcb1309197ab4"
MODEL_NAME = "v5_ru.pt"
MAX_MODEL_BYTES = 1024 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 60.0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="provision isolated Silero worker")
    parser.add_argument("--root", type=Path, default=Path("/opt/praxis-models/audio/silero"))
    parser.add_argument(
        "--requirements",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "requirements-silero.txt",
    )
    parser.add_argument("--model-url", default=MODEL_URL)
    parser.add_argument("--model-sha256", default=MODEL_SHA256)
    parser.add_argument("--force-venv", action="store_true")
    return parser


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _safe_absolute_path(path: Path, option: str, *, allow_root: bool = False) -> Path:
    try:
        expanded = path.expanduser()
    except (OSError, RuntimeError) as exc:
        raise SystemExit(f"{option} path cannot be expanded") from exc
    if not expanded.is_absolute() or "\x00" in str(expanded):
        raise SystemExit(f"{option} must be a safe absolute path")
    try:
        resolved = expanded.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise SystemExit(f"{option} path cannot be resolved") from exc
    if not allow_root and resolved == Path(resolved.anchor):
        raise SystemExit(f"{option} cannot be the filesystem root")
    return resolved


def _safe_model_url(raw: str) -> str:
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        raise SystemExit("--model-url must be a nonempty HTTPS URL")
    if any(
        ord(character) < 0x20 or ord(character) == 0x7f or character == "\\"
        for character in raw
    ):
        raise SystemExit("--model-url contains unsafe characters")
    try:
        parsed = urllib.parse.urlsplit(raw)
        # Accessing port detects malformed values that urlsplit otherwise preserves.
        parsed.port
    except ValueError as exc:
        raise SystemExit("--model-url is malformed") from exc
    hostname = parsed.hostname
    if (
        parsed.scheme != "https"
        or not raw.startswith("https://")
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or any(character.isspace() for character in hostname)
        or hostname in {"localhost", "localhost.localdomain"}
        or hostname.endswith(".localhost")
    ):
        raise SystemExit(
            "--model-url must be HTTPS with a host and without credentials or fragment"
        )
    return raw


def _content_length(response: BinaryIO) -> int | None:
    headers = getattr(response, "headers", None)
    raw = headers.get("Content-Length") if headers is not None else None
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("model response has an invalid Content-Length") from exc
    if value < 0:
        raise RuntimeError("model response has an invalid Content-Length")
    return value


def _download_once(url: str, destination: Path) -> None:
    """Download one bounded HTTPS artifact into a new temporary file."""

    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        final_url_getter = getattr(response, "geturl", None)
        final_url = final_url_getter() if callable(final_url_getter) else url
        if not isinstance(final_url, str):
            raise RuntimeError("model download returned an invalid final URL")
        try:
            _safe_model_url(final_url)
        except SystemExit as exc:
            raise RuntimeError("model download redirected to an unsafe URL") from exc
        declared_size = _content_length(response)
        if declared_size is not None and declared_size > MAX_MODEL_BYTES:
            raise RuntimeError(
                f"model response exceeds the {MAX_MODEL_BYTES}-byte download limit"
            )

        written = 0
        with destination.open("xb") as target:
            while True:
                chunk = response.read(min(1024 * 1024, MAX_MODEL_BYTES + 1 - written))
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_MODEL_BYTES:
                    raise RuntimeError(
                        f"model response exceeds the {MAX_MODEL_BYTES}-byte download limit"
                    )
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if declared_size is not None and written != declared_size:
            raise RuntimeError(
                f"model response length mismatch: declared {declared_size}, received {written}"
            )


def _download(url: str, destination: Path) -> None:
    """Download one bounded artifact and remove partial output on every failure."""

    destination.unlink(missing_ok=True)
    try:
        _download_once(url, destination)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    expected = args.model_sha256.strip().lower()
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        raise SystemExit("--model-sha256 must be 64 hexadecimal characters")
    model_url = _safe_model_url(args.model_url)
    requirements = _safe_absolute_path(args.requirements, "--requirements", allow_root=True)
    if not _regular_file(requirements):
        raise SystemExit(f"requirements file missing or not regular: {requirements}")
    root = _safe_absolute_path(args.root, "--root")
    root.mkdir(parents=True, exist_ok=True, mode=0o755)
    if not root.is_dir():
        raise SystemExit(f"--root is not a directory: {root}")

    model = root / MODEL_NAME
    partial = root / f".{MODEL_NAME}.download"
    try:
        model_stat = model.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise SystemExit(f"cannot inspect existing model: {model}") from exc
    else:
        if stat.S_ISREG(model_stat.st_mode) and model_stat.st_size > MAX_MODEL_BYTES:
            raise SystemExit(
                f"existing model exceeds the {MAX_MODEL_BYTES}-byte limit: {model}"
            )
    # Clean stale evidence even when venv/pip fails or an existing model verifies.
    partial.unlink(missing_ok=True)
    try:
        venv = root / ".venv"
        if venv.is_symlink():
            raise SystemExit(f"refusing unsafe symlink venv path: {venv}")
        if args.force_venv and venv.exists():
            if not venv.is_dir():
                raise SystemExit(f"refusing to remove unsafe venv path: {venv}")
            shutil.rmtree(venv)
        # A normal Python venv commonly uses an interpreter symlink.  The venv
        # directory itself is constrained above; following its bin/python is expected.
        if not (venv / "bin" / "python").is_file():
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        venv_python = venv / "bin" / "python"
        if not venv_python.is_file():
            raise RuntimeError(f"venv did not create an interpreter: {venv_python}")
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "-r", str(requirements)],
            check=True,
        )

        if _regular_file(model) and _sha256(model) == expected:
            print(f"model already verified: {model} sha256={expected}")
            return 0

        _download(model_url, partial)
        actual = _sha256(partial)
        if actual != expected:
            raise RuntimeError(
                f"downloaded model digest mismatch: expected {expected}, got {actual}"
            )
        partial.chmod(0o644)
        os.replace(partial, model)
        print(f"installed verified model: {model} sha256={expected}")
        return 0
    finally:
        partial.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
