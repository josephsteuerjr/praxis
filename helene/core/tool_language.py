"""Translate only reviewed stock schema prose; callable structure stays exact.

A changed or authored schema is returned literally. Its release coverage check
then names the stale projection rather than presenting an old translation.
"""
from __future__ import annotations

import copy
from collections import OrderedDict
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from string import Formatter
import threading

import frame_language

NATIVE_SCHEMA_LANGUAGE_API = 1
# All computer descriptions now describe the connected target, with Windows
# mechanics explicitly conditional. Legacy OS substring overlays must not
# invalidate these reviewed source schemas.
NEUTRAL_COMPUTER_SCHEMA_API = 1
_RUNTIME_LIMIT = 8
_runtime: dict[str, OrderedDict] = {}
_runtime_lock = threading.RLock()


@lru_cache(maxsize=1)
def registry() -> dict:
    return json.loads(Path(__file__).with_name('tool_language_registry.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def sources() -> dict:
    return json.loads(Path(__file__).with_name('tool_language_sources.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def native_registry() -> dict:
    """Installed harness tools, including distinct desktop/server shapes."""
    return json.loads(Path(__file__).with_name('native_tool_language_registry.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def worker_registry() -> dict:
    """Private Forge and frozen review/merge shapes, separate from main tools."""
    return json.loads(Path(__file__).with_name('worker_tool_language_registry.json').read_text(encoding='utf-8'))


def fingerprint(tool: dict) -> str:
    value = json.dumps(tool,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _rules(name):
    with _runtime_lock:
        variants = tuple(_runtime.get(name, {}).values())
    static = registry().get(name)
    return (variants + ((static,) if static else ()) + tuple(native_registry().get(name, ()))
            + tuple(worker_registry().get(name, ())))


def _raw_rule(tool):
    digest = fingerprint(tool)
    return next((rule for rule in _rules(tool.get('name')) if digest == rule['sha256']), None)


def _render(entry, language=None):
    if 'parts' in entry:
        return ''.join(_render(part, language) for part in entry['parts'])
    if not isinstance(entry.get('key'),str) or not entry['key']:
        raise ValueError('native template requires an owned catalog key')
    values = entry.get('values', {})
    if language is None:
        return frame_language.text(entry['key'], **values)
    return frame_language.catalog(language)[entry['key']].format_map(values)


def _at(value, path):
    for part in path:
        value = value[part]
    return value


def register_variant(before: dict, after: dict, *, descriptions: list, purpose=None) -> bool:
    """A trusted native adapter registers its exact resulting schema once.

    Ownership starts at a known stock source or a previously registered variant.
    Only the adapter changes callable fields. This layer subsequently projects
    description paths and literal substitutions, bounded to eight variants/name.
    Authored or changed input declines registration, before any adapter mutation.
    """
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    name = before.get('name')
    source = sources().get(name)
    previous = _raw_rule(before)
    if after.get('name') != name or not (previous or source and fingerprint(before) == source['raw']):
        return False
    entries = {}
    for entry in (previous or {}).get('descriptions', ()):
        path = tuple(entry['path'])
        try:
            old_parent,new_parent=_at(before,path[:-1]),_at(after,path[:-1])
            key=path[-1]
            if (isinstance(old_parent,dict) and isinstance(new_parent,dict)
                    and (key in old_parent)==(key in new_parent)
                    and old_parent.get(key)==new_parent.get(key)):
                entries[path] = copy.deepcopy(entry)
        except (KeyError, IndexError, TypeError):
            pass
    for supplied in descriptions:
        entry = copy.deepcopy(supplied)
        path = tuple(entry['path'])
        if not path or path[-1] != 'description':
            raise ValueError('native projection may only translate description paths')
        parent = _at(after, path[:-1])
        if not isinstance(parent, dict):
            raise ValueError('description parent must be an object')
        entry['present'] = path[-1] in parent
        entry['source'] = parent.get(path[-1])
        _validate_template(entry)
        entries[path] = entry
    purpose = copy.deepcopy(purpose if purpose is not None else (previous or {}).get('purpose'))
    if isinstance(purpose, str):
        purpose = {'key': purpose}
    if purpose is not None:
        _validate_template(purpose)
    rule = {'sha256': fingerprint(after), 'descriptions': list(entries.values()), 'purpose': purpose}
    with _runtime_lock:
        variants = _runtime.setdefault(name, OrderedDict())
        variants[rule['sha256']] = rule
        variants.move_to_end(rule['sha256'])
        while len(variants) > _RUNTIME_LIMIT:
            variants.popitem(last=False)
    return True


def _validate_template(entry, depth=0):
    if depth > 3 or not isinstance(entry, dict):
        raise ValueError('native template parts are too deep or invalid')
    if 'parts' in entry:
        parts = entry['parts']
        if ('key' in entry or 'values' in entry or not isinstance(parts, list)
                or not 1 <= len(parts) <= 4):
            raise ValueError('native composition requires one to four owned template parts')
        for part in parts:
            _validate_template(part, depth+1)
        return
    if not isinstance(entry.get('key'),str) or not entry['key']:
        raise ValueError('native template requires an owned catalog key')
    values = entry.get('values', {})
    if not isinstance(values, dict) or any(type(value) not in (str, int) for value in values.values()):
        raise ValueError('native substitutions must be literal strings or integers')
    for language in frame_language.LANGUAGES:
        template = frame_language.catalog(language)[entry['key']]
        fields = set()
        for _, field, spec, conversion in Formatter().parse(template):
            if field is not None:
                if not field.isidentifier() or spec or conversion:
                    raise ValueError('native template requires plain literal fields')
                fields.add(field)
        if fields != set(values):
            raise ValueError('native template parameters differ')


def accepts_variant(tool: dict) -> bool:
    """Whether an adapter may mutate this exact source, never authored prose."""
    source = sources().get(tool.get('name'))
    return bool(_raw_rule(tool) or source and fingerprint(tool) == source['raw'])


def description_template(tool: dict, path: list):
    """Return only a trusted owned template, never rendered or authored prose."""
    rule = _raw_rule(tool)
    for entry in (rule or {}).get('descriptions', ()):
        if entry['path'] == path:
            return copy.deepcopy({key:entry[key] for key in ('key','values','parts') if key in entry})
    return None


@frame_language.pinned
def apply(tools: list | None, *, fallback=None) -> list:
    result = []
    for tool in tools or ():
        rule = _raw_rule(tool) if isinstance(tool,dict) else None
        source = sources().get(tool.get('name')) if isinstance(tool,dict) else None
        if not rule:
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
            cursor[entry['path'][-1]] = _render(entry)
        result.append(translated)
    return result


@frame_language.pinned
def purpose(tool: dict) -> str | None:
    for rule in _rules(tool.get('name')):
        if not rule.get('purpose'):
            continue
        value = _purpose(tool, rule)
        if value is not None:
            return value
    return None


def _purpose(tool, rule):
    label = rule['purpose']
    if isinstance(label, str):
        label = {'key': label}
    if fingerprint(tool)==rule['sha256']:
        return _render(label)
    # The caller may already hold the translated schema. Normalize only exact
    # reviewed descriptions to their source before checking the complete shape.
    original = copy.deepcopy(tool)
    try:
        for entry in rule['descriptions']:
            cursor = original
            for part in entry['path'][:-1]:
                cursor = cursor[part]
            allowed = [_render(entry, language)
                       for language in frame_language.LANGUAGES]
            if cursor[entry['path'][-1]] not in allowed:
                return None
            if entry.get('present',True):
                cursor[entry['path'][-1]] = entry.get('source',_render(entry, 'en'))
            else:
                del cursor[entry['path'][-1]]
    except (KeyError,IndexError,TypeError):
        return None
    return _render(label) if fingerprint(original)==rule['sha256'] else None


def known(tool: dict) -> bool:
    source=sources().get(tool.get('name'))
    return bool(_raw_rule(tool) or source and fingerprint(tool) in (source['raw'],source['english']))


def coverage(tools: list, *, names=None) -> dict:
    by_name = {tool.get('name'):tool for tool in tools if isinstance(tool,dict)}
    selected={name:rule for name,rule in registry().items() if names is None or name in names}
    missing = [name for name in selected if name not in by_name]
    stale = [name for name,rule in selected.items()
             if name in by_name and fingerprint(by_name[name]) != rule['sha256']]
    return {'reviewed':len(selected),'missing':missing,'stale':stale}


def native_coverage(tools: list) -> dict:
    """Qualification census includes every variant, even duplicate tool names."""
    actual = {(tool.get('name'), fingerprint(tool)) for tool in tools if isinstance(tool, dict)}
    variants = [(name, rule) for name, rules in native_registry().items() for rule in rules]
    return {'reviewed': len(variants), 'missing': [rule['variant'] for name, rule in variants
            if (name, rule['sha256']) not in actual]}
