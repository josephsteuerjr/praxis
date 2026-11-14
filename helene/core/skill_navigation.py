"""Edition skill locator: the disk owns existence, the authored INDEX owns purpose.

This adapter does not install or interpret a skill. The existing capabilities
parser owns normal navigation; this seam refuses to call a lossy decoder a clean
INDEX read in the Hélène model frame. No automatic map goes to non-owner turns.
"""
from __future__ import annotations

from pathlib import Path

import capabilities
import frame_language


@frame_language.pinned
def owner_map(index_path: Path) -> str:
    index = Path(index_path)
    if index.exists():
        try:
            before = index.read_bytes()
            before.decode("utf-8")  # the parser's errors='ignore' must not hide corruption
        except (OSError, UnicodeError) as exc:
            return frame_language.text('skills.index_unreadable', error_type=type(exc).__name__)
        result = capabilities.skill_navigation(index)
        try:
            if index.read_bytes() != before:
                return frame_language.text('skills.index_changed')
        except OSError:
            return frame_language.text('skills.index_unavailable')
        return result
    return capabilities.skill_navigation(index)
