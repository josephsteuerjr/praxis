"""One owner-window command over the local Python file-dialog implementation.

This is an internal child of the native window, not an HTTP endpoint.  A request
is one bounded JSON document on stdin; stdout is one JSON receipt.  The desktop
package places localharness/ and deskd/ beside one another under app/.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deskd import local_files  # noqa: E402


_ACTIONS = {"list", "read", "save"}


def handle(request: object) -> dict:
    if not isinstance(request, dict) or request.get("action") not in _ACTIONS:
        raise ValueError("Неизвестное действие файлового диалога")
    action = request["action"]
    if action == "list":
        path = request.get("path", "")
        if not isinstance(path, str):
            raise ValueError("Выбери абсолютный путь к папке")
        return local_files.listing(path, native=True)
    if action == "read":
        path = request.get("path")
        if not isinstance(path, str):
            raise ValueError("Выбери абсолютный путь к файлу")
        return local_files.read_file(path, native=True)
    for key in ("folder", "name", "data"):
        if not isinstance(request.get(key), str):
            raise ValueError("Файловый диалог не получил содержимое файла")
    if type(request.get("overwrite")) is not bool:
        raise ValueError("Файловый диалог не получил решение о замене")
    return local_files.save_preview(request["folder"], request["name"],
                                    request["data"], request["overwrite"])


def main() -> int:
    try:
        # 64 MiB of content expands to at most 89.5 MiB base64; an entire
        # framing object gets a small bounded allowance beyond that.
        limit = ((local_files.MAX_FILE + 2) // 3) * 4 + 65536
        raw = sys.stdin.buffer.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("Файл больше 64 МБ")
        request = json.loads(raw.decode("utf-8"))
        result = {"ok": True, "result": handle(request)}
    except (OSError, ValueError, TypeError) as exc:
        # A failed save can have committed before an observation/response error;
        # the native host does not automatically retry any dispatched save.
        result = {"ok": False, "error": str(exc),
                  "code": ("file_exists" if isinstance(exc, FileExistsError) else
                           "outcome_unknown" if isinstance(exc, local_files.FileOutcomeUnknown)
                           else "file_error")}
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
