"""Read-only evidence collection. Executed inside the owned search process."""
from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tokenize
import unicodedata

import code_query_cache as cache
import code_query_facts as facts
import forge_intelligence as intelligence

VERSION = 'code-query-1:' + facts.VERSION
OPS = {'symbols','definition','references','callers','callees','impact','overview'}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(root, path):
    if not path.resolve().is_relative_to(root):
        raise ValueError('symlink_outside_root')
    return path.read_bytes()


def decode(raw, python=False):
    encoding=tokenize.detect_encoding(io.BytesIO(raw).readline)[0] if python else 'utf-8'
    return raw.decode(encoding)


def candidates(raw, name, module):
    if not name:return True
    needles=[name.rsplit('.',1)[-1], *([module] if module else [])]
    if any(n.encode('utf-8') in raw for n in needles):return True
    # Python normalizes identifiers to NFKC; non-ASCII spelling may differ from AST names.
    if not raw.isascii():
        try:return any(n in unicodedata.normalize('NFKC',decode(raw,True)) for n in needles)
        except (UnicodeError,SyntaxError):return True
    return False


def inventory(root, scope, budget, skipped, op):
    suffixes=set(intelligence.LANG_BY_EXT) if op not in ('overview','impact') else None
    paths=intelligence.source_files(root,limit=budget.files,budget=budget,audit=skipped,suffixes=suffixes)
    absent=[r for r in skipped if r['reason']=='unsupported_suffix']
    if absent:
        skipped[:]=[r for r in skipped if r['reason']!='unsupported_suffix']
        skipped.append({'reason':'unsupported_suffix','count':len(absent)})
    return [p for p in paths if p==scope or p.is_relative_to(scope)]


def manifest(root, paths, budget, failed):
    result={}
    for p in paths:
        if budget.expired():
            budget.stage('hash files',taken=len(result),seen=len(paths),exhausted=False,stopped='deadline');break
        try:result[p.relative_to(root).as_posix()]=digest(read(root,p))
        except (OSError,ValueError,RuntimeError) as exc:
            failed.append({'path':p.relative_to(root).as_posix(),'reason':type(exc).__name__})
    return result


def changed(before,after):
    return sorted(k for k in before.keys()|after.keys() if before.get(k)!=after.get(k))


def _name_match(row,name):
    return not name or row.get('qualname')==name or row.get('name')==name


def _row(fact, **more):
    row={k:v for k,v in dict(fact,**more).items() if k not in ('scope','chain')}
    row.setdefault('qualname',row.get('name',''))
    return row


def _text_rows(text, ranges, name):
    lines=[s.rstrip('\r') for s in text.split('\n')]
    rx=re.compile(r'(?<![\w$])'+re.escape(name.rsplit('.',1)[-1])+r'(?![\w$])')
    for span in ranges:
        sl,sc=span['start'];el,ec=span['end']
        for no in range(sl,el+1):
            line=lines[no-1];lo=sc if no==sl else 0;hi=ec if no==el else len(line)
            for match in rx.finditer(line,lo,hi):
                start={'line':no,'char':match.start(),'byte':len(line[:match.start()].encode('utf-8'))}
                yield dict(start=start,end={'line':no,'char':match.end()},name=match.group(),qualname='',kind='mention',
                           evidence='text_mention',method='text',ambiguous=False)


def _python_rows(parsed,text,op,name,module,file_module,package):
    if op in ('definition','symbols'):
        for item in parsed['definitions']:
            if _name_match(item,name):
                yield _row(item,evidence='definition',method='python_ast',ambiguous=False,
                    matches_target=file_module==module if module else None)
        return
    if op=='callees':
        if module and file_module!=module:return
        selected={r['qualname'] for r in parsed['definitions'] if _name_match(r,name)
                  and r['kind'] in ('function','async_function','class')}
        for item in parsed['calls']:
            if item['caller'] in selected:
                yield facts.classify(item,parsed,file_module,package,item['name'],'')
        return
    for item in parsed['calls'] + (parsed['references'] if op=='references' else []):
        classified=facts.classify(item,parsed,file_module,package,name,module)
        bound=classified.get('binding',{}).get('name','')
        if item['name']==name.rsplit('.',1)[-1] or bound==name:
            yield classified
    if op=='references':
        for item in parsed['imports']:
            resolved=facts.import_module(item,file_module,package)
            if item['imported']==name or (module and resolved==module):
                yield _row(item,kind='import',evidence='import_candidate',method='python_ast',ambiguous=not bool(resolved),
                    binding={'module':resolved,'name':item['imported'],'local':item['name']},
                    matches_target=(resolved==module and item['imported']==name) if module else None)
        yield from _text_rows(text,parsed['text_ranges'],name)


def _generic_rows(root,p,text,op,name,budget,skipped):
    language=intelligence.LANG_BY_EXT.get(p.suffix.lower(),'')
    rx=intelligence._GENERIC_SYMBOLS.get(language)
    lines=[s.rstrip('\r') for s in text.split('\n')]
    if op in ('symbols','definition') and rx:
        for no,line in enumerate(lines,1):
            match=rx.match(line)
            if match and (not name or match.group(2)==name):
                yield {'name':match.group(2),'qualname':match.group(2),'kind':'regex',
                       'evidence':'definition','method':'regex','ambiguous':True,
                       'start':{'line':no,'char':match.start(2),'byte':len(line[:match.start(2)].encode('utf-8'))},
                       'end':{'line':no,'char':match.end(2)}}
    elif op=='references':
        # Reuse Forge's exact word/reference scan, with its inherited byte cap disclosed.
        if p.stat().st_size>4*1024*1024:
            skipped.append({'path':p.relative_to(root).as_posix(),'reason':'legacy_text_reference_4MiB_cap'});return
        found=intelligence.references(root,name,p.relative_to(root).as_posix(),limit=max(1,len(lines)+1),budget=budget)
        for row in found.get('references',[]):
            no=row['line'];column=lines[no-1].find(name.rsplit('.',1)[-1])
            if column>=0:
                yield {'name':name,'qualname':'','kind':'mention','evidence':'text_mention','method':'text','ambiguous':True,
                       'start':{'line':no,'char':column,'byte':len(lines[no-1][:column].encode('utf-8'))},'end':None}


def execute(request, *, budget=None):
    root=Path(request['root']['path']).resolve();op=request['op'];name=request.get('name','');module=request.get('module','')
    requested=(root/request.get('path','')).resolve()
    scope=root if op=='impact' else requested
    if op not in OPS or not requested.is_relative_to(root) or not requested.exists():
        return {'error':'invalid_query','complete':False}
    own=budget or intelligence.Budget(label='code_query')
    skipped=[];failed=[];dynamic=[];grammar=set();rows=[]
    stats={'hits':0,'misses':0,'write_errors':0,'persistent':bool(request.get('cache_dir')),
           'capacity_bytes':cache.CACHE_BYTES,'idle_days':cache.CACHE_DAYS}
    paths=inventory(root,scope,own,skipped,op)
    first=manifest(root,paths,own,failed)
    old=request.get('previous')
    if old is not None:
        delta=changed(old,first)
        if delta:return {'error':'stale_cursor','changed_paths':delta,'complete':False,'coverage':{'seen':len(first),'failed':failed,'skipped':skipped,'budget':own.report()}}
    binding={k:request.get(k,'') for k in ('op','path','name','module')}
    snapshot=digest(json.dumps([VERSION,str(root),binding,sorted(first.items())],ensure_ascii=False,separators=(',',':')).encode())[:16]
    own.inventory=(root,paths)
    parsed_count=0;prefiltered=0;non_code=0
    # All reads contributing rows are compared against the initial manifest.
    for rel,sha in first.items():
        if own.expired():break
        p=root/rel;language=intelligence.LANG_BY_EXT.get(p.suffix.lower(),'')
        if not language:
            non_code+=1;continue
        try:
            raw=read(root,p)
            if digest(raw)!=sha:return {'error':'snapshot_changed','changed_paths':[rel],'complete':False}
            if op not in ('overview','impact') and not candidates(raw,name,module if op=='references' else ''):
                prefiltered+=1;continue
            if b'\0' in raw:
                skipped.append({'path':rel,'reason':'binary'});continue
            text=decode(raw,language=='python')
            if language=='python':
                parsed=cache.get(sha,text,request.get('cache_dir',''),stats)
                parsed_count+=1
                file_module=intelligence._python_module(p,root)
                found=[] if op in ('overview','impact') else _python_rows(parsed,text,op,name,module,file_module,p.name=='__init__.py')
                dynamic.extend({'path':rel,'start':r['start'],'reason':'dynamic_import_not_resolved'} for r in parsed['dynamic_imports'])
            else:
                grammar.add(language)
                found=[] if op in ('overview','impact') else _generic_rows(root,p,text,op,name,own,skipped)
                if op in ('callers','callees'):
                    skipped.append({'path':rel,'reason':'grammar_unavailable_for_calls'})
            for row in found:
                rows.append(dict(row,path=rel,file_sha256=sha))
        except (SyntaxError,tokenize.TokenError,UnicodeError,ValueError,OSError,RecursionError) as exc:
            failed.append({'path':rel,'reason':type(exc).__name__,'line':getattr(exc,'lineno',None)})
    detail=None
    if op=='overview':
        detail=intelligence.project_overview(root,budget=own)
        rows=[{'path':k,'kind':'directory','evidence':'inventory','method':'heuristic','ambiguous':True,**v} for k,v in detail['tree'].items()]
        detail['display_caps']={'entrypoints':100,'central_modules':40,'hotspots':40,'test_sample':80,'docs':80}
    elif op=='impact':
        detail=intelligence.impact(root,changed=[request['path']] if request.get('path') else None,budget=own)
        rows=[{'path':p,'kind':'affected','evidence':'heuristic','method':'heuristic','ambiguous':True} for p in detail['impacted']]
        # Keep the authoritative full gate distinct from this heuristic.
        detail['verification']=intelligence.verification_plan(root,full=True,budget=own)
        failed.extend({'path':p,'reason':'legacy_impact_parse_error'} for p in detail['parse_errors'])
        detail['display_caps']={'impacted':500,'tests':100,'parse_errors':50,'reverse_modules':2000}
        if len(detail['impacted'])>=500 or len(detail['tests'])>=100 or len(detail['parse_errors'])>=50:
            skipped.append({'path':'.','reason':'legacy_impact_display_cap'})
    if prefiltered:skipped.append({'reason':'name_byte_prefilter','count':prefiltered})
    if non_code:skipped.append({'reason':'non_code_file','count':non_code})
    own.stage('semantic evidence',taken=parsed_count,seen=len(first),exhausted=not own.expired(),stopped='deadline' if own.expired() else '')
    # Re-inventory detects additions/deletions; re-hash catches same-size, restored-mtime edits.
    verify_skips=[];verify_failed=[]
    own.inventory=None
    second_paths=inventory(root,scope,own,verify_skips,op)
    second=manifest(root,second_paths,own,verify_failed)
    delta=changed(first,second)
    stable=not delta and not verify_failed and own.complete()
    if delta and not own.expired():
        return {'error':'snapshot_changed','changed_paths':delta,'complete':False,
                'coverage':{'seen':len(first),'failed':failed+verify_failed,'skipped':skipped,'budget':own.report()}}
    rows.sort(key=lambda r:(r.get('ambiguous',False),r.get('path',''),r.get('start',{}).get('line',0),r.get('start',{}).get('char',0),r.get('evidence','')))
    offset=int(request.get('offset',0));limit=request.get('limit',40)
    page=rows[offset:offset+limit]
    # Fresh source only for displayed snippets, never cached.
    for row in page:
        if 'file_sha256' not in row:continue
        try:
            raw=read(root,root/row['path'])
            if digest(raw)!=row['file_sha256']:
                return {'error':'snapshot_changed','changed_paths':[row['path']],'complete':False}
            lines=decode(raw,(root/row['path']).suffix.lower() in ('.py','.pyi')).split('\n')
            row['snippet']=lines[row['start']['line']-1].rstrip('\r').strip()[:160]
        except (OSError,UnicodeError,SyntaxError,ValueError,IndexError):
            return {'error':'snapshot_changed','changed_paths':[row['path']],'complete':False}
    counts={}
    for r in rows:counts[r['method']]=counts.get(r['method'],0)+1
    incomplete_reasons={'io_error','missing_or_unreadable','gitignore_inventory_failed','grammar_unavailable_for_calls','legacy_text_reference_4MiB_cap','legacy_impact_display_cap','binary'}
    complete=stable and not failed and not any(r['reason'] in incomplete_reasons for r in skipped)
    git=intelligence._git_root(root)
    head=''
    if git:
        try:head=subprocess.check_output(['git','-C',str(git),'rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL,timeout=max(.01,min(5,own.left()))).strip()
        except (OSError,subprocess.SubprocessError):pass
    result={'op':op,'root':dict(request['root'],git_root=str(git) if git else None,head=head),
            'snapshot':snapshot,'rows':page,'total':len(rows),
            'next_cursor':f'{snapshot}:{offset+limit}' if stable and offset+limit<len(rows) else None,
            'complete':complete,'coverage':{'seen':len(first),'parsed':parsed_count,'failed':failed+verify_failed,
                'skipped':skipped,'prefiltered':prefiltered,'dynamic_imports':dynamic,'missing_grammars':sorted(grammar),
                'budget':own.report(),'complete':complete,
                'limits':['Evidence collection only; no proven call edges or type resolution.',
                          'No transitive re-export resolution. Scope writes/conditional imports remain ambiguous.',
                          'Prefiltered files were not parsed; syntax coverage is only for candidates.',
                          'Coordinates: line 1-based, char Unicode code point, byte UTF-8 column; end exclusive, CRLF excluded.']},
            'method_summary':counts,'cache':stats,'_manifest':first if stable else None}
    if detail is not None:result['detail']=detail
    cache.collect(request.get('cache_dir',''),own)
    return result


if __name__=='__main__':
    import sys
    request=json.load(sys.stdin)
    try:result=execute(request)
    except Exception as exc:
        result={'error':'query_failed','reason':type(exc).__name__,'complete':False}
    sys.stdout.write(json.dumps(result,ensure_ascii=False))
