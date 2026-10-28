#!/usr/bin/env python3
"""Fail-closed, bounded Silero soak gate for the isolated worker/client.

The public CLI deliberately accepts only a real 30--60 minute run.  Unit tests use
``main(..., _allow_short_duration=True)``; that private in-process seam is never
reachable from command-line arguments.  Every passing run has synthesized audio,
observed all four mandatory resource rails, completed its configured duration and
proved idle unload.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import time
import wave
from collections import Counter, deque
from pathlib import Path
from typing import Any

# Running ``python scripts/silero_soak.py`` makes scripts/ sys.path[0].
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from silero_tts_client import SileroTTSClient, SileroTTSConfig

MIN_DURATION_MINUTES = 30.0
MAX_DURATION_MINUTES = 60.0
MAX_REQUESTS = 10_000
MAX_FAILURE_DETAILS = 25
MAX_SAMPLE_RECORDS = 250
MAX_KEPT_AUDIO = 100
DEFAULT_MAX_KEPT_AUDIO_BYTES = 64 * 1024 * 1024
MAX_KEPT_AUDIO_BYTES = 512 * 1024 * 1024
MAX_REPORT_BYTES = 256 * 1024
MAX_CORPUS_BYTES = 256 * 1024
MAX_CORPUS_LINES = 1_000
MAX_CORPUS_LINE_CHARS = 4_000
MAX_IDLE_WAIT_SECONDS = 600.0
MAX_REQUEST_TIMEOUT_SECONDS = 120.0

DEFAULT_CORPUS = (
    "Праксис проверяет сложные имена: Арет, Хоуп, Розенфельд и Ксения; "
    "числа 48 килогерц, 22 августа 2026 года, паузы — и ударения.",
    "В четверг четвёртого числа щука проглотила щётку; ещё, конечно, ёж и подъём.",
    "Съешь же ещё этих мягких французских булок, да выпей чаю — без спешки.",
)


def _positive_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return value


def _duration_minutes(raw: str) -> float:
    value = _positive_float(raw)
    if value > MAX_DURATION_MINUTES:
        raise argparse.ArgumentTypeError(
            f"must not exceed {MAX_DURATION_MINUTES:g} minutes"
        )
    return value


def _idle_wait_seconds(raw: str) -> float:
    value = _positive_float(raw)
    if value > MAX_IDLE_WAIT_SECONDS:
        raise argparse.ArgumentTypeError(
            f"must not exceed {MAX_IDLE_WAIT_SECONDS:g} seconds"
        )
    return value


def _nonnegative_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("must be a finite nonnegative number")
    return value


def _nonnegative_int(raw: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return value


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def _request_limit(raw: str) -> int:
    value = _positive_int(raw)
    if value > MAX_REQUESTS:
        raise argparse.ArgumentTypeError(f"must not exceed {MAX_REQUESTS}")
    return value


def _kept_audio_limit(raw: str) -> int:
    value = _nonnegative_int(raw)
    if value > MAX_KEPT_AUDIO:
        raise argparse.ArgumentTypeError(f"must not exceed {MAX_KEPT_AUDIO}")
    return value


def _kept_audio_bytes_limit(raw: str) -> int:
    value = _nonnegative_int(raw)
    if value > MAX_KEPT_AUDIO_BYTES:
        raise argparse.ArgumentTypeError(
            f"must not exceed {MAX_KEPT_AUDIO_BYTES} bytes"
        )
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="isolated Silero soak gate")
    parser.add_argument("--duration-minutes", type=_duration_minutes, default=30.0)
    parser.add_argument("--interval-seconds", type=_nonnegative_float, default=5.0)
    parser.add_argument("--idle-wait-seconds", type=_idle_wait_seconds)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--max-failures", type=_nonnegative_int, default=0)
    parser.add_argument("--max-requests", type=_request_limit, default=MAX_REQUESTS)
    parser.add_argument("--max-worker-rss-bytes", type=_positive_int, required=True)
    parser.add_argument("--max-cgroup-current-bytes", type=_positive_int, required=True)
    parser.add_argument("--max-cgroup-swap-bytes", type=_nonnegative_int, required=True)
    parser.add_argument("--max-psi-some-avg10", type=_nonnegative_float, required=True)
    parser.add_argument("--keep-audio", action="store_true")
    parser.add_argument(
        "--max-kept-audio", type=_kept_audio_limit, default=10,
        help=f"maximum retained WAV files with --keep-audio (hard maximum {MAX_KEPT_AUDIO})",
    )
    parser.add_argument(
        "--max-kept-audio-bytes",
        type=_kept_audio_bytes_limit,
        default=DEFAULT_MAX_KEPT_AUDIO_BYTES,
        help=(
            "aggregate retained WAV bytes with --keep-audio "
            f"(hard maximum {MAX_KEPT_AUDIO_BYTES})"
        ),
    )
    return parser


def _safe_report_path(path: Path) -> Path:
    try:
        expanded = path.expanduser()
    except (OSError, RuntimeError) as exc:
        raise SystemExit("--report path cannot be expanded") from exc
    if not expanded.is_absolute() or "\x00" in str(expanded):
        raise SystemExit("--report must be a safe absolute path")
    try:
        resolved = expanded.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise SystemExit("--report path cannot be resolved") from exc
    if resolved == Path(resolved.anchor):
        raise SystemExit("--report cannot be the filesystem root")
    return resolved


def _read_int(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="ascii").strip()
    except OSError:
        return None
    if raw == "max":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _read_psi_avg10(path: Path) -> float | None:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except OSError:
        return None
    for line in lines:
        if not line.startswith("some "):
            continue
        for field in line.split()[1:]:
            if field.startswith("avg10="):
                try:
                    value = float(field.split("=", 1)[1])
                    return value if math.isfinite(value) and value >= 0 else None
                except ValueError:
                    return None
    return None


def _sample_cgroup() -> dict[str, int | float | None]:
    # Resolve the process's actual cgroup-v2 directory rather than assuming it is
    # the hierarchy root (systemd/deploy containers are commonly nested).
    cgroup_root = Path("/sys/fs/cgroup")
    root = cgroup_root
    try:
        for line in Path("/proc/self/cgroup").read_text(encoding="ascii").splitlines():
            hierarchy, controllers, raw_path = line.split(":", 2)
            if hierarchy == "0" and controllers == "":
                candidate = Path(raw_path.lstrip("/"))
                if ".." in candidate.parts:
                    break
                nested = cgroup_root / candidate
                # Container /proc may expose a host-relative path while its cgroup
                # mount is already namespaced to `/`; fall back only in that case.
                if (nested / "memory.current").is_file():
                    root = nested
                break
    except (OSError, ValueError):
        pass
    return {
        "current_bytes": _read_int(root / "memory.current"),
        "swap_bytes": _read_int(root / "memory.swap.current"),
        "psi_some_avg10": _read_psi_avg10(root / "memory.pressure"),
    }


def _audio_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as audio:
        seconds = audio.getnframes() / audio.getframerate()
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("synthesized WAV has no audio")
    return seconds


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_corpus(path: Path | None) -> tuple[str, ...]:
    if path is None:
        return DEFAULT_CORPUS
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError(f"cannot stat corpus: {path}") from exc
    if size > MAX_CORPUS_BYTES:
        raise ValueError(f"corpus exceeds {MAX_CORPUS_BYTES} bytes")
    selected: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if not clean or line.lstrip().startswith("#"):
            continue
        if len(clean) > MAX_CORPUS_LINE_CHARS:
            raise ValueError(
                f"corpus line exceeds {MAX_CORPUS_LINE_CHARS} characters"
            )
        selected.append(clean)
        if len(selected) > MAX_CORPUS_LINES:
            raise ValueError(f"corpus exceeds {MAX_CORPUS_LINES} entries")
    lines = tuple(selected)
    if not lines:
        raise ValueError("corpus is empty")
    return lines


def _maximum(current: float | int | None, value: Any) -> float | int | None:
    if value is None:
        return current
    return value if current is None else max(current, value)


def _resource_failures(sample: dict[str, Any], args: argparse.Namespace) -> list[str]:
    rails = (
        ("worker_rss", sample.get("worker_peak_rss_bytes"), args.max_worker_rss_bytes),
        ("cgroup_current", sample.get("current_bytes"), args.max_cgroup_current_bytes),
        ("cgroup_swap", sample.get("swap_bytes"), args.max_cgroup_swap_bytes),
        ("psi", sample.get("psi_some_avg10"), args.max_psi_some_avg10),
    )
    failures: list[str] = []
    for name, observed, limit in rails:
        if observed is None:
            failures.append(f"{name}_unavailable")
        elif observed > limit:
            failures.append(f"{name}_limit_exceeded")
    return failures


def _top_failure_types(counter: Counter[str]) -> dict[str, int]:
    most_common = counter.most_common(10)
    result = dict(most_common)
    omitted = sum(counter.values()) - sum(result.values())
    if omitted:
        result["Other"] = omitted
    return result


def _encode_report(report: dict[str, Any]) -> bytes:
    """Encode bounded evidence, degrading to aggregates rather than failing to report."""

    def encode() -> bytes:
        return json.dumps(
            report, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")

    payload = encode()
    # These lists are evidence only; aggregate counts/maxima/checks remain intact.
    for key, omitted_key in (
        ("samples", "sample_records_omitted"),
        ("failures", "failure_details_omitted"),
        ("artifacts", "artifact_records_omitted"),
    ):
        while len(payload) > MAX_REPORT_BYTES and report[key]:
            removed = max(1, len(report[key]) // 2)
            del report[key][-removed:]
            report["evidence"][omitted_key] += removed
            report["evidence"]["report_truncated"] = True
            payload = encode()
    if len(payload) > MAX_REPORT_BYTES:
        raise RuntimeError("aggregate Silero soak report exceeds hard size limit")
    return payload


def main(
    argv: list[str] | None = None, *, _allow_short_duration: bool = False
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    args.report = _safe_report_path(args.report)
    if args.duration_minutes < MIN_DURATION_MINUTES and not _allow_short_duration:
        parser.error(
            f"--duration-minutes must be between {MIN_DURATION_MINUTES:g} and "
            f"{MAX_DURATION_MINUTES:g}"
        )

    corpus = _load_corpus(args.corpus)
    config = SileroTTSConfig.from_env(
        output_dir=Path(os.environ.get("PRAXIS_TTS_OUTPUT_DIR", "/tmp/praxis-silero-soak"))
    )
    if config.request_timeout_seconds > MAX_REQUEST_TIMEOUT_SECONDS:
        raise SystemExit(
            "Silero request timeout exceeds the bounded soak maximum of "
            f"{MAX_REQUEST_TIMEOUT_SECONDS:g} seconds"
        )
    client = SileroTTSClient(config)
    duration_seconds = args.duration_minutes * 60.0
    started_wall = time.time()
    started = time.monotonic()
    deadline = started + duration_seconds
    latencies: list[float] = []
    rtfs: list[float] = []
    failure_details: list[dict[str, str]] = []
    failure_types: Counter[str] = Counter()
    failure_count = 0
    successful_requests = 0
    artifacts: list[Path] = []
    kept_audio_bytes = 0
    artifacts_omitted = 0
    request_count = 0
    sample_count = 0
    sample_head: list[dict[str, Any]] = []
    sample_tail: deque[dict[str, Any]] = deque(maxlen=MAX_SAMPLE_RECORDS // 2)
    stop_reasons: list[str] = []
    maxima: dict[str, float | int | None] = {
        "worker_rss_bytes": None,
        "worker_peak_rss_bytes": None,
        "cgroup_current_bytes": None,
        "cgroup_swap_bytes": None,
        "psi_some_avg10": None,
    }
    post_idle: dict[str, int | float | None] = {
        "current_bytes": None,
        "swap_bytes": None,
        "psi_some_avg10": None,
    }
    idle_unloaded = False
    duration_completed = False
    post_clear_status: dict[str, Any] = {}

    try:
        while True:
            now = time.monotonic()
            if request_count and now >= deadline:
                duration_completed = True
                break
            if request_count >= args.max_requests:
                stop_reasons.append("request_limit_reached_before_duration")
                break

            text = corpus[request_count % len(corpus)]
            before = time.monotonic()
            artifact: Path | None = None
            try:
                artifact = client.synthesize(text)
                latency = time.monotonic() - before
                audio_seconds = _audio_seconds(artifact)
                successful_requests += 1
                latencies.append(latency)
                rtfs.append(latency / audio_seconds)
                if args.keep_audio:
                    artifact_bytes = artifact.stat().st_size
                    within_count = len(artifacts) < args.max_kept_audio
                    within_bytes = (
                        artifact_bytes <= args.max_kept_audio_bytes - kept_audio_bytes
                    )
                    if within_count and within_bytes:
                        artifacts.append(artifact)
                        kept_audio_bytes += artifact_bytes
                        artifact = None
                    else:
                        artifacts_omitted += 1
            except Exception as exc:  # stable, bounded detail; corpus stays private
                failure_count += 1
                failure_type = type(exc).__name__[:100]
                failure_types[failure_type] += 1
                if len(failure_details) < MAX_FAILURE_DETAILS:
                    failure_details.append({
                        "type": failure_type,
                        "message": str(exc)[:200],
                    })
            finally:
                if artifact is not None:
                    artifact.unlink(missing_ok=True)
            request_count += 1

            status = client.status()
            cgroup = _sample_cgroup()
            sample = {
                "monotonic_seconds": time.monotonic() - started,
                "worker_pid": status.get("pid"),
                "worker_rss_bytes": status.get("rss_bytes"),
                "worker_peak_rss_bytes": status.get("peak_rss_bytes"),
                **cgroup,
            }
            sample_count += 1
            if len(sample_head) < MAX_SAMPLE_RECORDS // 2:
                sample_head.append(sample)
            else:
                sample_tail.append(sample)
            maxima["worker_rss_bytes"] = _maximum(
                maxima["worker_rss_bytes"], sample["worker_rss_bytes"]
            )
            maxima["worker_peak_rss_bytes"] = _maximum(
                maxima["worker_peak_rss_bytes"], sample["worker_peak_rss_bytes"]
            )
            maxima["cgroup_current_bytes"] = _maximum(
                maxima["cgroup_current_bytes"], sample["current_bytes"]
            )
            maxima["cgroup_swap_bytes"] = _maximum(
                maxima["cgroup_swap_bytes"], sample["swap_bytes"]
            )
            maxima["psi_some_avg10"] = _maximum(
                maxima["psi_some_avg10"], sample["psi_some_avg10"]
            )

            resource_failures = _resource_failures(sample, args)
            if resource_failures:
                stop_reasons.extend(resource_failures)
                break
            if failure_count > args.max_failures:
                stop_reasons.append("failure_budget_exceeded")
                break
            if time.monotonic() >= deadline:
                duration_completed = True
                break
            if request_count >= args.max_requests:
                stop_reasons.append("request_limit_reached_before_duration")
                break
            if args.interval_seconds:
                time.sleep(min(args.interval_seconds, max(0.0, deadline - time.monotonic())))

        # A breached rail must clear immediately, not spend an idle timeout above it.
        if not stop_reasons:
            idle_wait = args.idle_wait_seconds
            if idle_wait is None:
                idle_wait = min(
                    MAX_IDLE_WAIT_SECONDS,
                    config.idle_timeout_seconds
                    + max(2.0, config.terminate_grace_seconds),
                )
            idle_deadline = time.monotonic() + idle_wait
            while client.pid is not None and time.monotonic() < idle_deadline:
                time.sleep(0.1)
            idle_unloaded = client.pid is None
            post_idle = _sample_cgroup()
        else:
            post_idle = _sample_cgroup()
    finally:
        client.clear_cache()
        post_clear_status = client.status()

    checks = {
        "duration_completed": duration_completed,
        "successful_synthesis": successful_requests > 0,
        "failures_within_budget": failure_count <= args.max_failures,
        "request_bound_not_hit": "request_limit_reached_before_duration" not in stop_reasons,
        "idle_unloaded": idle_unloaded,
        "worker_rss_within_limit": (
            maxima["worker_peak_rss_bytes"] is not None
            and maxima["worker_peak_rss_bytes"] <= args.max_worker_rss_bytes
        ),
        "cgroup_current_within_limit": (
            maxima["cgroup_current_bytes"] is not None
            and maxima["cgroup_current_bytes"] <= args.max_cgroup_current_bytes
        ),
        "cgroup_swap_within_limit": (
            maxima["cgroup_swap_bytes"] is not None
            and maxima["cgroup_swap_bytes"] <= args.max_cgroup_swap_bytes
        ),
        "psi_within_limit": (
            maxima["psi_some_avg10"] is not None
            and maxima["psi_some_avg10"] <= args.max_psi_some_avg10
        ),
    }
    passed = all(checks.values())
    retained_samples = sample_head + list(sample_tail)
    report = {
        "schema": "praxis.silero.soak.v2",
        "passed": passed,
        "started_epoch": started_wall,
        "duration_seconds": time.monotonic() - started,
        "configured_duration_seconds": duration_seconds,
        "requests": request_count,
        "successful_requests": successful_requests,
        "failure_count": failure_count,
        "failures": failure_details,
        "failure_types": _top_failure_types(failure_types),
        "samples": retained_samples,
        "stop_reasons": list(dict.fromkeys(stop_reasons)),
        "latency_seconds": {
            "min": min(latencies) if latencies else None,
            "median": statistics.median(latencies) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
        "rtf": {
            "min": min(rtfs) if rtfs else None,
            "median": statistics.median(rtfs) if rtfs else None,
            "max": max(rtfs) if rtfs else None,
        },
        "limits": {
            "max_requests": args.max_requests,
            "max_failures": args.max_failures,
            "max_worker_rss_bytes": args.max_worker_rss_bytes,
            "max_cgroup_current_bytes": args.max_cgroup_current_bytes,
            "max_cgroup_swap_bytes": args.max_cgroup_swap_bytes,
            "max_psi_some_avg10": args.max_psi_some_avg10,
            "max_kept_audio": args.max_kept_audio,
            "max_kept_audio_bytes": args.max_kept_audio_bytes,
            "max_report_bytes": MAX_REPORT_BYTES,
        },
        "maxima": maxima,
        "post_idle": post_idle,
        "idle_unloaded": idle_unloaded,
        "checks": checks,
        "client_status": post_clear_status,
        "artifacts": [
            {
                "name": path.name[:200],
                "bytes": path.stat().st_size if path.is_file() else None,
                "sha256": _file_sha256(path) if path.is_file() else None,
            }
            for path in artifacts
        ],
        "evidence": {
            "sample_records": sample_count,
            "sample_records_omitted": sample_count - len(retained_samples),
            "failure_details_omitted": failure_count - len(failure_details),
            "artifact_records_omitted": 0,
            "audio_artifacts_not_kept": artifacts_omitted,
            "retained_audio_bytes": kept_audio_bytes,
            "report_truncated": False,
        },
        "model": str(config.model)[:200],
        "model_sha256": config.model_sha256,
        "speaker": str(config.speaker)[:200],
        "sample_rate": config.sample_rate,
    }
    payload = _encode_report(report)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    partial = args.report.with_name(f".{args.report.name}.part")
    try:
        partial.write_bytes(payload)
        os.replace(partial, args.report)
    finally:
        partial.unlink(missing_ok=True)
    print(json.dumps({
        "passed": passed,
        "requests": request_count,
        "successful_requests": successful_requests,
        "failures": failure_count,
        "idle_unloaded": idle_unloaded,
        "report": str(args.report),
    }, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
