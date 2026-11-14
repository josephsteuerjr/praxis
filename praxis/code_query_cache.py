"""Disposable structural facts; no snippets, source text or literal values."""
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
import code_query_facts as facts

CACHE_DAYS = 30
CACHE_BYTES = 256 * 1024 * 1024
_MEMORY = {}


def get(sha, text, directory, stats):
    key=hashlib.sha256((facts.VERSION+':'+sha).encode()).hexdigest()
    path=Path(directory)/key[:2]/(key+'.json') if directory else None
    value=_MEMORY.get(key)
    if value is None and path:
        try:
            data=json.loads(path.read_text(encoding='utf-8'))
            if data.get('version')==facts.VERSION and data.get('sha256')==sha:
                candidate=data['facts']
                if all(isinstance(candidate.get(k),list) for k in ('definitions','calls','references','scopes','imports','text_ranges','dynamic_imports')):
                    value=candidate
        except (OSError,ValueError,TypeError,KeyError):pass
    if value is not None:
        stats['hits']+=1
        if path:
            try:os.utime(path,None)
            except OSError:pass
        return value
    value=facts.parse(text);stats['misses']+=1
    # RAM belongs to this disposable owned search process.
    _MEMORY[key]=value
    if path:
        temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary.write_text(json.dumps({'version':facts.VERSION,'sha256':sha,'facts':value},ensure_ascii=False),encoding='utf-8')
            os.replace(temporary,path)
        except OSError:
            stats['write_errors']+=1
        finally:
            try:temporary.unlink(missing_ok=True)
            except OSError:pass
    return value


def collect(directory, budget):
    """Best-effort cache GC. These are cache capacity limits, never search row limits."""
    if not directory:return
    base=Path(directory);entries=[];total=0;now=time.time()
    try:
        for path in base.glob('*/*.json'):
            if budget.expired():return
            stat=path.stat();entries.append((stat.st_mtime,path,stat.st_size));total+=stat.st_size
        for at,path,size in sorted(entries):
            if budget.expired():return
            if now-at>CACHE_DAYS*86400 or total>CACHE_BYTES:
                path.unlink(missing_ok=True);total-=size
    except OSError:pass
