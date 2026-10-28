"""Exact line tail without reading an entire growing archive into memory."""
from pathlib import Path


def tail_lines(path: Path, count: int) -> list[str]:
    count = max(1, int(count))
    try:
        with Path(path).open('rb') as src:
            src.seek(0, 2)
            end = src.tell()
            chunks = []
            breaks = 0
            while end > 0 and breaks <= count:
                size = min(65536, end)
                end -= size
                src.seek(end)
                chunk = src.read(size)
                chunks.append(chunk)
                breaks += chunk.count(b'\n')
    except OSError:
        return []
    lines = b''.join(reversed(chunks)).decode('utf8', 'replace').split('\n')
    if lines and lines[-1] == '': lines.pop()
    return [line.rstrip('\r') for line in lines[-count:]]
