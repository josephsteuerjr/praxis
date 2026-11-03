"""Preserve authored app/harness/resources against their exact shipped base."""
from pathlib import Path
import os,shutil,tempfile,time
from . import authored_baseline,source_merge as merge
from .package_notice import read,write


def binding(old,new):
    from .update_composition import app_inventory
    old,new=Path(old).resolve(),Path(new).resolve()
    original=authored_baseline.validate(old)['app']['files']
    incoming=authored_baseline.validate(new)['app']['files']
    return {'old':str(old),'base':original,'local':app_inventory(old/'app'),'incoming':incoming}


def changed(old,new):
    value=binding(old,new)
    return value if value['local']!=value['base'] else None


def seed(directory,operation,state):
    """Only the inert M surface changes; the incoming verifier stays pristine."""
    from .update_composition import app_inventory
    directory=Path(directory);plan=read(directory/'plan.json');selected=plan.get('app_source')
    if not selected:return
    incoming=Path(state['program'])
    if binding(selected['old'],incoming)!=selected:raise ValueError('Authored app inputs changed before composition')
    if app_inventory(incoming/'app')!=selected['incoming'] or state['base_app']!=selected['incoming']:
        raise ValueError('Incoming app differs from its pristine baseline')
    app_plan=directory/'app-merge';old=Path(selected['old'])
    if app_plan.exists():
        frozen=merge.validate_inputs(app_plan)
        if frozen['files']!={key:selected[key] for key in ('base','local','incoming')}:
            raise ValueError('Application merge belongs to other source bytes')
    else:
        with tempfile.TemporaryDirectory(prefix='app-baseline-',dir=directory) as temporary:
            authored_baseline.extract(old,'app',temporary)
            frozen=merge.create(temporary,old/'app',incoming/'app',app_plan,
                exe=merge.executable(incoming,old),application=True)
    if frozen['files']!={key:selected[key] for key in ('base','local','incoming')}:
        raise ValueError('Application inputs changed during snapshot creation')
    destination=directory/'compound/app'
    if app_inventory(destination)!=state['base_app']:raise ValueError('App candidate changed before authored seeding')
    for path in sorted(destination.rglob('*'),key=lambda p:len(p.parts),reverse=True):
        if path.is_file():path.unlink()
        elif path.is_dir():path.rmdir()
    for here,dirs,names in os.walk(app_plan/'result'):
        for name in names:
            source=Path(here)/name;out=destination/source.relative_to(app_plan/'result')
            out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,out)
    state['app_source']={'binding':selected,'input_sha256':merge.input_signature(frozen)}


def validate_inputs(directory,state):
    selected=state.get('app_source')
    if not selected:return None
    directory=Path(directory);value=merge.validate_inputs(directory/'app-merge',selected['input_sha256'])
    if binding(selected['binding']['old'],state['program'])!=selected['binding']:
        raise ValueError('Authored app changed after preparation')
    return value


def record_resolution(directory,name,*,reason,side=''):
    """Mirror an admitted compound repair into its app A/L/B resolution record."""
    from .update_composition import app_inventory
    directory=Path(directory);state=read(directory/'composition.json')
    if not state.get('app_source'):return
    validate_inputs(directory,state);plan=read(directory/'app-merge/plan.json');merge.relative(name)
    target=directory/'app-merge/result'/name;selected=directory/'compound/app'/name
    before=app_inventory(directory/'app-merge/result').get(name)
    if selected.is_file():
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(selected,target)
    else:target.unlink(missing_ok=True)
    after=app_inventory(directory/'app-merge/result').get(name)
    if name in plan['conflicts']:plan['resolved'][name]={'side':side or 'authored','after':after}
    else:plan.setdefault('repairs',[]).append({'path':name,'before':before,'after':after,'reason':reason,'at':time.time()})
    plan['state']='conflicts';plan.pop('result',None);plan.pop('merged_commit',None)
    write(directory/'app-merge/plan.json',plan)


def resolve_side(directory,name,side,*,expected,reason):
    """Choose exact frozen bytes, including binary resources, without decoding."""
    from . import update_composition as composition
    directory=Path(directory);merge.relative(name)
    if side not in ('base','local','incoming'):raise ValueError('Choose an immutable application side')
    state=read(directory/'composition.json');plan=validate_inputs(directory,state)
    if plan is None:raise ValueError('This composition has no authored app merge')
    if not isinstance(reason,str) or not reason.strip() or composition.signature(directory)!=expected:
        raise ValueError('Use the current composition and a resolution reason')
    declared=set().union(*(set(files) for files in plan['files'].values()))
    if name not in declared:raise ValueError('Not an application source input')
    source=directory/'app-merge'/side/name;target=directory/'compound/app'/name
    before=composition._hash(target) if target.is_file() else None
    if source.is_file():
        raw=source.read_bytes()
        if name.endswith('.py'):compile(raw,name,'exec')
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    else:target.unlink(missing_ok=True)
    state['repairs'].append({'path':'app/'+name,'reason':reason,'side':side,'before':before,
        'after':composition._hash(target) if target.is_file() else None,'composition_before_sha256':expected,'at':time.time()})
    write(directory/'composition.json',state);record_resolution(directory,name,reason=reason,side=side)


def finish(directory):
    from .update_composition import app_inventory
    directory=Path(directory);state=read(directory/'composition.json')
    if not state.get('app_source'):return
    validate_inputs(directory,state)
    result=merge.finish(directory/'app-merge',exe=merge.executable(state['program']))
    if app_inventory(directory/'compound/app')!=result['result']:
        raise ValueError('Compound application differs from its resolved app merge')


def require_ready(directory):
    from .update_composition import app_inventory
    directory=Path(directory);state=read(directory/'composition.json')
    value=validate_inputs(directory,state)
    if value is None:return
    if value.get('state')!='ready' or app_inventory(directory/'app-merge/result')!=value.get('result'):
        raise ValueError('Finish every application conflict before verification')
    if app_inventory(directory/'compound/app')!=value['result']:
        raise ValueError('Application resolution changed after finishing')
