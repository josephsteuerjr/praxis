"""Lossless Telegram chunk boundaries shared by live sends and durable plans.

This is not a Markdown renderer or a content filter. Complete links and code
spans which fit in one chunk stay together; oversized/malformed spans retain the
old bounded, lossless fallback. Persisted plans keep their original chunks.
"""
from __future__ import annotations

import re

# Code has precedence, so link-looking text in a code block is not interpreted.
# Telethon's Markdown URL labels accept newlines as well as spaces.
_MARKUP_START = re.compile(r"```|`|\[[^\]]*\]\(")


def _markup_spans(text: str):
    pos = 0
    while match := _MARKUP_START.search(text, pos):
        begin = match.start()
        token = match.group()
        if token in ("`", "```"):
            close = text.find(token, match.end())
            if close < 0:
                pos = match.end()
                continue
            end = close + len(token)
        else:
            depth = 1
            end = match.end()
            while end < len(text) and depth:
                char = text[end]
                if char.isspace():
                    break
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                end += 1
            if depth:
                pos = match.end()
                continue
        yield begin, end
        pos = end


def split_text(text: str, limit: int = 3800) -> tuple[str, ...]:
    text = str(text or "")
    if not text:
        return ()
    if limit < 1:
        raise ValueError("Telegram text limit must be positive")
    # Prefix sums make span-size checks constant-time, including astral emoji.
    units = [0]
    for char in text:
        units.append(units[-1] + (2 if ord(char) > 0xFFFF else 1))
    spans = [(a, b) for a, b in _markup_spans(text)
             if units[b] - units[a] <= limit]
    chunks = []
    start = span_index = 0
    while start < len(text):
        hard_end = start
        while hard_end < len(text) and units[hard_end + 1] - units[start] <= limit:
            hard_end += 1
        if hard_end == start:
            raise ValueError("one character exceeds the Telegram text limit")
        split_at = hard_end
        if hard_end < len(text):
            floor = start + max(1, (hard_end - start) // 2)
            paragraph = text.rfind("\n\n", floor, hard_end)
            newline = text.rfind("\n", floor, hard_end)
            if paragraph >= floor:
                split_at = paragraph + 2
            elif newline >= floor:
                split_at = newline + 1
            else:
                for pos in range(hard_end - 1, floor - 1, -1):
                    if text[pos].isspace():
                        split_at = pos + 1
                        break
            while span_index < len(spans) and spans[span_index][1] <= split_at:
                span_index += 1
            if span_index < len(spans):
                begin, end = spans[span_index]
                if begin < split_at < end:
                    # Prefer finishing the span if it fits, otherwise move the
                    # whole span into the next chunk. Never emit an empty chunk.
                    split_at = end if end <= hard_end else begin
        chunks.append(text[start:split_at])
        start = split_at
    return tuple(chunks)
