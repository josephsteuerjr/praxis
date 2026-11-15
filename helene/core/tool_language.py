"""Translate only reviewed stock schema prose; callable structure stays exact.

A changed or authored schema is returned literally. Its release coverage check
then names the stale projection rather than presenting an old translation.
"""
from __future__ import annotations

import copy
from functools import lru_cache
import hashlib
import json
from pathlib import Path

import frame_language


@lru_cache(maxsize=1)
def registry() -> dict:
    return json.loads(Path(__file__).with_name('tool_language_registry.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def sources() -> dict:
    return json.loads(Path(__file__).with_name('tool_language_sources.json').read_text(encoding='utf-8'))


def fingerprint(tool: dict) -> str:
    value = json.dumps(tool,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


@frame_language.pinned
def apply(tools: list | None, *, fallback=None) -> list:
    result = []
    for tool in tools or ():
        rule = registry().get(tool.get('name')) if isinstance(tool,dict) else None
        source = sources().get(tool.get('name')) if isinstance(tool,dict) else None
        if not rule or fingerprint(tool) != rule['sha256']:
            # A pending stock translation may use the legacy projection. Never
            # hand authored/changed schemas to an overlay that replaces their text.
            if fallback and source and fingerprint(tool)==source['raw']:
                result.extend(fallback([tool]))
                continue
            result.append(tool)
            continue
        translated = copy.deepcopy(tool)
        for entry in rule['descriptions']:
            cursor = translated
            for part in entry['path'][:-1]:
                cursor = cursor[part]
            cursor[entry['path'][-1]] = frame_language.text(entry['key'])
        result.append(translated)
    return result


@frame_language.pinned
def purpose(tool: dict) -> str | None:
    rule = registry().get(tool.get('name'))
    if not rule:
        return None
    if fingerprint(tool)==rule['sha256']:
        return frame_language.text(rule['purpose'])
    # The caller may already hold the translated schema. Normalize only exact
    # reviewed descriptions to their source before checking the complete shape.
    original = copy.deepcopy(tool)
    try:
        for entry in rule['descriptions']:
            cursor = original
            for part in entry['path'][:-1]:
                cursor = cursor[part]
            allowed = [frame_language.catalog(language)[entry['key']].format()
                       for language in frame_language.LANGUAGES]
            if cursor[entry['path'][-1]] not in allowed:
                return None
            if entry.get('present',True):
                cursor[entry['path'][-1]] = entry.get('source',frame_language.catalog('en')[entry['key']].format())
            else:
                del cursor[entry['path'][-1]]
    except (KeyError,IndexError,TypeError):
        return None
    return frame_language.text(rule['purpose']) if fingerprint(original)==rule['sha256'] else None


def known(tool: dict) -> bool:
    source=sources().get(tool.get('name'))
    return bool(source and fingerprint(tool) in (source['raw'],source['english']))


def coverage(tools: list) -> dict:
    by_name = {tool.get('name'):tool for tool in tools if isinstance(tool,dict)}
    missing = [name for name in registry() if name not in by_name]
    stale = [name for name,rule in registry().items()
             if name in by_name and fingerprint(by_name[name]) != rule['sha256']]
    return {'reviewed':len(registry()),'missing':missing,'stale':stale}
