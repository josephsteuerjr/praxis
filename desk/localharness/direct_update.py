"""One code transition: selected core variants and one authored application.

Per-core Git plans remain immutable A/L/B inputs, not independent coordinators.
The first selected core owns the existing pipeline/hand. Nothing here schedules
an agent or interprets test/reviewer findings; those keep their existing owners.
"""
from pathlib import Path
import shutil
from .package_notice import read, write
from . import source_merge as merge

FORMAT = 'helene-direct-update.v1'


def group(directory):
    directory = Path(directory).resolve()
    plan = read(directory/'plan.json')
    value = plan.get('direct_update')
    if not value:
        return None
    if value.get('format') != FORMAT or not value.get('members'):
        raise ValueError('Invalid direct update composition')
    members = value['members']
    if (len({r['directory'] for r in members}) != len(members)
            or str(directory) not in {r['directory'] for r in members}
            or value['leader'] != members[0]['directory']):
        raise ValueError('Direct update member identity differs')
    for row in members:
        peer = Path(row['directory']).resolve()
        other = read(peer/'plan.json')
        if (other.get('direct_update') != value or str(peer) != row['directory']
                or other.get('local_path') != row['source']):
            raise ValueError('Direct update selected cores changed')
    return value


def leader(directory):
    value = group(directory)
    return Path(value['leader']) if value else Path(directory).resolve()


def protocol(plans,previous):
    """Retained admission chooses compatibility; preparation alone does not."""
    direct=False;legacy=False
    for row in plans:
        root=Path(row['directory']);plan=merge.validate_inputs(root)
        if plan.get('direct_update'):direct=True
        elif any((root/name).exists() for name in ('pipeline.json','agent-resolution.json','composition.json','cancel.json')):legacy=True
    if direct and legacy:raise ValueError('Mixed admitted update protocols require recovery; no inputs changed')
    if direct:return FORMAT
    if legacy or (previous and not previous.get('direct_update')):return 'legacy-per-core'
    return FORMAT


def bind(new, rows, profiles=None):
    """Publish the transition once, before admitting its selected agent."""
    if not rows:
        return None
    members = [{'directory': str(Path(r['directory']).resolve()),
                'source': str(Path(r['source']).resolve()),
                'tree':str(Path(r['tree']).resolve())} for r in rows]
    value = {'format': FORMAT, 'program': str(Path(new).resolve()),
             'leader': members[0]['directory'], 'members': members,
             'profiles':profiles or [{'tree':r['tree'],'source':r['source']} for r in members]}
    pending=[]
    for row in members:
        path = Path(row['directory'])/'plan.json'
        plan = merge.validate_inputs(path.parent)
        if plan.get('direct_update') not in (None, value):
            raise ValueError('Another direct composition owns the source plan')
        plan['direct_update'] = value
        pending.append((path,plan))
    for path,plan in pending:write(path,plan)
    return value


def core_rows(directory, *, fresh=False):
    value = group(directory)
    rows = value['members'] if value else [{'directory':str(Path(directory).resolve())}]
    result = []
    for index, row in enumerate(rows):
        root = Path(row['directory'])
        plan = merge.validate_inputs(root)
        if fresh and merge.inventory(plan['local_path']) != plan['files']['local']:
            raise ValueError('Local code changed for selected core during update preparation')
        result.append({'key':'core-'+str(index), 'directory':str(root),
                       'input_sha256':merge.input_signature(plan),
                       'files':merge.inventory(root/'result')})
    return result


def finish(directory, exe):
    for row in core_rows(directory, fresh=True):
        root = Path(row['directory'])
        plan = read(root/'plan.json')
        if plan.get('state') != 'ready':
            merge.finish(root, exe=exe)
        elif merge.inventory(root/'result') != plan.get('result'):
            raise ValueError('Finished selected core changed')


def require_ready(directory):
    for row in core_rows(directory, fresh=True):
        plan = read(Path(row['directory'])/'plan.json')
        if plan.get('state') != 'ready' or row['files'] != plan.get('result'):
            raise ValueError('Finish every selected core before verification/apply')


def operation(directory, original):
    """A core test view, never a second mutable pipeline or approval."""
    root = Path(directory)
    plan = merge.validate_inputs(root)
    return {**original, 'id':root.name, 'directory':str(root),
            'source':plan['local_path'], 'input_sha256':merge.input_signature(plan),
            'candidate_sha256':merge.candidate_signature(root)}


def materials(directory):
    """Existing review A/L/B/M navigation, qualified by selected core identity."""
    directory = leader(directory)
    destination = directory/'review-cores'
    roots = {}
    rows = core_rows(directory, fresh=True)
    for side, label in (('base','base'),('local','mine'),('incoming','release'),('result','resolved')):
        root = destination/side
        declared = {}
        published = {}
        for row in rows:
            plan = merge.validate_inputs(row['directory'])
            files = row['files'] if side == 'result' else plan['files'][side]
            names = set().union(*(set(s) for s in plan['files'].values()), set(row['files']))
            for name in sorted(names):
                logical = row['key']+'/'+name
                entry = files.get(name)
                declared[logical] = entry
                published[logical] = sorted({r['sha256'] for s in ('base','incoming')
                    if (r := plan['files'][s].get(name))})
                target = root/logical
                if entry is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(Path(row['directory'])/side/name,target)
        # A prior review's removed paths must not survive in the next subject.
        if root.exists():
            for path in root.rglob('*'):
                if path.is_file() and path.relative_to(root).as_posix() not in declared:
                    path.unlink()
        roots[label] = {'root':str(root),'merge_files':declared,'published_files':published}
    return roots


def controls(directory):
    from . import source_update
    value=group(directory)
    if not value:return {'stop':False,'pause':False}
    return {'stop':any(source_update._cancelled(Path(r['directory'])) for r in value['members']),
            'pause':any(source_update._owner_blocked(r['tree']) for r in value['profiles'])}


def blocked(directory):
    return any(controls(directory).values())


def automatic(directory):
    for row in core_rows(directory,fresh=True):
        plan=read(Path(row['directory'])/'plan.json')
        if (plan.get('state')!='ready' or plan.get('repairs') or plan.get('app_source')
                or row['files']!=plan.get('result')):return False
    return True


def conflicts(directory):
    result=[];repairs=[]
    for row in core_rows(directory):
        plan=read(Path(row['directory'])/'plan.json')
        result.extend(row['key']+'/'+n for n in plan['conflicts'])
        repairs.extend({**r,'path':row['key']+'/'+r['path']} for r in plan.get('repairs',[]))
    return result,repairs


def core_command(directory,action,args,operation):
    """One hand edits qualified core M paths; no per-core lifecycle state."""
    import difflib
    from . import update_composition,agent_source_update
    name=args.get('path','')
    key,separator,relative=name.partition('/')
    rows={r['key']:r for r in core_rows(directory)}
    if key not in rows or not separator:return False,None
    merge.relative(relative);root=Path(rows[key]['directory'])
    plan=merge.validate_inputs(root)
    if relative not in set().union(*(set(f) for f in plan['files'].values())):
        raise ValueError('Not a declared selected-core input')
    if action in ('read','diff'):
        def text(side):
            if side not in ('base','local','incoming','result'):raise ValueError('Unknown core side')
            path=root/side/relative
            if not path.is_file():return ''
            value=path.read_text(encoding='utf-8-sig')
            from .self_review import source_credential,PRIVATE_NAMES
            if any(p.lower() in PRIVATE_NAMES for p in Path(relative).parts) or source_credential(value,published=side in ('base','incoming')):
                raise ValueError('Private selected-core content is withheld')
            return value
        value=(text(args.get('side') or 'result') if action=='read' else
            ''.join(difflib.unified_diff(text('local').splitlines(True),text('result').splitlines(True),fromfile='local/'+name,tofile='result/'+name)))
        offset=max(0,int(args.get('offset',0)));end=min(len(value),offset+8000)
        return True,{'text':value[offset:end],'offset':offset,'total_chars':len(value),'next_offset':end if end<len(value) else None}
    if action not in ('resolve','patch'):return False,None
    if args.get('expected_composition_sha256')!=update_composition.signature(directory):
        raise ValueError('Use the exact whole expected_composition_sha256')
    content=args.get('content')
    if action=='patch':
        value=(root/'result'/relative).read_text(encoding='utf8')
        before,after=args.get('before'),args.get('after')
        if not isinstance(before,str) or not before or not isinstance(after,str) or value.count(before)!=1:
            raise ValueError('Patch must match one exact core fragment')
        content=value.replace(before,after,1)
    if plan.get('state')=='ready':merge.reopen(root)
    merge.resolve(root,relative,content,delete=args.get('delete') is True,side=args.get('side',''),
        reason=args.get('reason',''),expected_candidate=merge.candidate_signature(root))
    agent_source_update._invalidate(directory,operation)
    return True,{'state':'waiting_agent','core':key,'composition_sha256':update_composition.signature(directory)}
