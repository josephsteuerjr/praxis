# -*- coding: utf-8 -*-
"""Full local Settings document writer for the desktop shell.

The shell transports the document through stdin (not argv), and owns the UI and
relay lifecycle. This is deliberately not deskd.agentcfg's restricted server writer.
The mtime comparison and replacement share one per-config transaction with the
channel writer, so two concurrent saves cannot both accept the same old receipt.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


def mtime_ns(path: Path) -> str | None:
    try:
        return str(path.stat().st_mtime_ns)
    except FileNotFoundError:
        return None


def save(path: Path, document: str, seen: str | None) -> dict:
    value = json.loads(document)
    if not isinstance(value,dict):raise ValueError('Settings must be a JSON object')
    if __package__:
        from .relay_network import environment
    else:
        from relay_network import environment
    # Validate before replacing the document: an input error must not tear
    # down the working supervisor on its next roster refresh.
    relay=value.get('relay')
    environment({} if relay is None else relay)
    if 'self_review' in value:
        if __package__:
            from .self_review import settings
        else:
            from self_review import settings
        value['self_review'] = settings({'self_review': value['self_review']})
    pretty = json.dumps(value, ensure_ascii=False, indent=2)
    if __package__:
        from .config_transaction import transaction
    else:
        from localharness.config_transaction import transaction
    with transaction(path):
        return _commit(path, pretty, seen)


def _commit(path, pretty, seen):
    seen = seen.strip() if seen else None
    if seen:
        current = mtime_ns(path)
        if current is not None and seen != current:
            return {
                "ok": False,
                "code": "stale",
                "mtime_ns": current,
                "error": "Настройки на диске изменились с тех пор, как окно их открыло — перечитай и перенеси правку заново.",
            }
    # Keep the staging file in the target directory: replace must stay on the
    # same filesystem. Never leave a stale .tmp-helene.json shared by writers.
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         prefix=".tmp-helene-", dir=path.parent,
                                         delete=False) as output:
            staged = Path(output.name)
            output.write(pretty)
        os.replace(staged, path)
        staged = None
        fresh = mtime_ns(path)
        if fresh is None:
            raise OSError("отпечаток записанного файла не читается")
        return {"ok": True, "mtime_ns": fresh}
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="write the local full Settings document")
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--mtime-ns", default="")
    args = parser.parse_args(argv)
    try:
        result = save(args.path, sys.stdin.read(), args.mtime_ns)
    except (OSError, ValueError) as exc:
        # The caller cannot infer from an exit failure that no replacement took
        # place. It must not retry or reconcile the relay without a receipt.
        print(f"запись настроек не подтверждена: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
