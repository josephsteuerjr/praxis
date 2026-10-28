"""Read-only audit for duplicated outgoing Telegram messages.

The group archive is append-only canonical evidence.  This utility scans its
``praxis.group.message.v1`` records and reports *exact* repeated outgoing texts
whose Telegram delivery route differs.  It does not edit the archive or infer
that a duplicate is necessarily a transport retry: independent authored turns
can legitimately produce the same text, so the output is a review queue.

Examples::

    python telegram_duplicate_audit.py --days 7
    python telegram_duplicate_audit.py --since 2026-08-05T00:00:00Z
    python telegram_duplicate_audit.py --archive-root memory/groups --json
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

MESSAGE_SCHEMA = "praxis.group.message.v1"
UTC = dt.timezone.utc


@dataclass(frozen=True)
class OutgoingMessage:
    archive: str
    line: int
    timestamp: str
    peer_id: str
    topic_id: int | None
    message_id: int
    reply_to_message_id: int | None
    text: str

    @property
    def route(self) -> tuple[str, int | None, int | None]:
        return (self.peer_id, self.topic_id, self.reply_to_message_id)

    @property
    def text_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def parse_timestamp(value: object) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty ISO-8601 string")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def iter_outgoing_messages(
    archive_root: Path,
    *,
    since: dt.datetime,
) -> tuple[list[OutgoingMessage], int]:
    """Return outgoing text records at/after ``since`` and malformed-line count."""
    messages: list[OutgoingMessage] = []
    malformed = 0
    for archive in sorted(archive_root.glob("*/archive.jsonl")):
        try:
            lines = archive.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            malformed += 1
            continue
        for line_number, line in enumerate(lines, 1):
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("not an object")
                if row.get("schema") != MESSAGE_SCHEMA or row.get("kind") != "message":
                    continue
                if row.get("outgoing") is not True or not isinstance(row.get("text"), str):
                    continue
                timestamp = parse_timestamp(row.get("timestamp"))
                if timestamp < since:
                    continue
                peer_id = str(row["peer_id"])
                message_id = int(row["message_id"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                malformed += 1
                continue
            messages.append(
                OutgoingMessage(
                    archive=str(archive),
                    line=line_number,
                    timestamp=timestamp.isoformat().replace("+00:00", "Z"),
                    peer_id=peer_id,
                    topic_id=_optional_int(row.get("topic_id")),
                    message_id=message_id,
                    reply_to_message_id=_optional_int(row.get("reply_to_message_id")),
                    text=row["text"],
                )
            )
    return messages, malformed


def find_exact_cross_route_duplicates(
    messages: Iterable[OutgoingMessage],
) -> list[list[OutgoingMessage]]:
    """Group exact repeated texts only when they were sent to distinct routes."""
    by_text: dict[str, list[OutgoingMessage]] = defaultdict(list)
    for message in messages:
        by_text[message.text].append(message)
    findings = [
        sorted(group, key=lambda item: (item.timestamp, item.message_id))
        for group in by_text.values()
        if len({item.route for item in group}) > 1
    ]
    return sorted(findings, key=lambda group: (group[0].timestamp, group[0].message_id))


def build_report(
    archive_root: Path,
    *,
    since: dt.datetime,
) -> dict[str, object]:
    messages, malformed = iter_outgoing_messages(archive_root, since=since)
    findings = find_exact_cross_route_duplicates(messages)
    return {
        "schema": "praxis.telegram.duplicate-audit.v1",
        "archive_root": str(archive_root),
        "since": since.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "outgoing_messages": len(messages),
        "malformed_records": malformed,
        "exact_cross_route_duplicate_clusters": [
            {
                "text_sha256": group[0].text_sha256,
                "text": group[0].text,
                "messages": [asdict(message) for message in group],
            }
            for group in findings
        ],
    }


def _default_archive_root() -> Path:
    return Path(__file__).resolve().parent / "memory" / "groups"


def _render_human(report: dict[str, object]) -> str:
    lines = [
        f"Период с {report['since']}",
        f"Исходящих текстовых записей: {report['outgoing_messages']}",
        f"Неразобранных записей: {report['malformed_records']}",
    ]
    clusters = report["exact_cross_route_duplicate_clusters"]
    assert isinstance(clusters, list)
    lines.append(f"Точных дублей с разными маршрутами: {len(clusters)}")
    for index, cluster in enumerate(clusters, 1):
        assert isinstance(cluster, dict)
        text = str(cluster["text"])
        digest = str(cluster["text_sha256"])
        lines.extend((
            "",
            f"[{index}] sha256={digest}",
            f"Текст: {text!r}",
        ))
        messages = cluster["messages"]
        assert isinstance(messages, list)
        for message in messages:
            assert isinstance(message, dict)
            lines.append(
                "  {timestamp} message=#{message_id} peer={peer_id} "
                "topic={topic_id} reply_to={reply_to_message_id} "
                "{archive}:{line}".format(**message)
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, default=_default_archive_root())
    period = parser.add_mutually_exclusive_group()
    period.add_argument("--days", type=float, default=7.0,
                        help="rolling UTC period; default: 7")
    period.add_argument("--since", help="inclusive ISO-8601 UTC/offset timestamp")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    if args.days is not None and args.days < 0:
        parser.error("--days must not be negative")
    try:
        since = (parse_timestamp(args.since) if args.since else
                 dt.datetime.now(UTC) - dt.timedelta(days=args.days))
    except ValueError as exc:
        parser.error(str(exc))
    report = build_report(args.archive_root, since=since)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_render_human(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
