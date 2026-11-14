"""Verify reviewed stock translations and report untranslated resource paths.

The manifest binds exact canonical and translated bytes. Updating it is an
author review operation, never an automatic step of a release build.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re

LANGUAGES=('en','fr','zh')


def stock_paths(resources):
    resources=Path(resources)
    paths={'SOUL.md','self-day-zero.md'}
    for section in ('soul','memory'):
        base=resources/section
        if base.is_dir():
            paths.update(path.relative_to(resources).as_posix() for path in base.rglob('*')
                         if path.is_file() and not any(part.startswith(('.', '__'))
                            for part in path.relative_to(base).parts))
    return sorted(paths)


def translated_path(resources,relative,language):
    path=Path(relative);parts=path.parts
    if parts[0] in ('soul','memory'):
        return Path(resources)/(parts[0]+'.'+language)/Path(*parts[1:])
    return Path(resources)/path.with_name(path.stem+'.'+language+path.suffix)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit(resources):
    resources=Path(resources)
    manifest=json.loads((resources/'stock-languages.json').read_text(encoding='utf-8'))
    if manifest.get('format')!='helene-stock-languages.v1':
        raise ValueError('Unsupported stock translation manifest')
    records=manifest['files'];paths=stock_paths(resources);missing={code:[] for code in LANGUAGES}
    if set(records)-set(paths):raise ValueError('Unknown canonical stock path')
    for relative in paths:
        record=records.get(relative)
        source=resources/relative
        present={code:translated_path(resources,relative,code) for code in LANGUAGES}
        for code,path in present.items():
            if not path.is_file():missing[code].append(relative)
            elif record is None:raise ValueError('Unreviewed stock translation: '+relative+':'+code)
        if record is None:continue
        if digest(source)!=record['ru']:raise ValueError('Canonical stock changed: '+relative)
        canonical=source.read_text(encoding='utf-8')
        params=lambda value:Counter(re.findall(r'\{\{\w+\}\}',value))
        codes=lambda value:Counter(re.findall(r'(?<!`)`([^`\n]+)`(?!`)',value))
        links=lambda value:Counter(re.findall(r'\]\(([^)]+)\)',value))
        for code,path in present.items():
            if not path.is_file() or digest(path)!=record[code]:
                raise ValueError('Stock translation changed or missing: '+relative+':'+code)
            value=path.read_text(encoding='utf-8')
            if params(value)!=params(canonical):raise ValueError('Stock placeholders differ: '+relative+':'+code)
            if record.get('identifiers') and (codes(value)!=codes(canonical) or links(value)!=links(canonical)):
                raise ValueError('Stock identifiers differ: '+relative+':'+code)
    # An orphan translation must not evade the manifest simply because its
    # source was deleted or renamed.
    for section in ('soul','memory'):
        for code in LANGUAGES:
            base=resources/(section+'.'+code)
            if base.is_dir():
                for path in base.rglob('*'):
                    if path.is_file() and section+'/'+path.relative_to(base).as_posix() not in records:
                        raise ValueError('Orphan stock translation: '+str(path))
    return {'canonical_files':len(paths),'reviewed_files':len(records),'missing':missing,
            'complete':not any(missing.values())}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--require-complete',action='store_true')
    args=parser.parse_args();result=audit(Path(__file__).resolve().parents[1]/'resources')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if args.require_complete and not result['complete']:raise SystemExit('Stock translation coverage is incomplete')
