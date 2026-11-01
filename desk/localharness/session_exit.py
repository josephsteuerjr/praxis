"""Durable whole-programme Exit; distinct from detach and update cancellation."""
from pathlib import Path
import hashlib,os,time
from .package_notice import read,write

FORMAT='helene-session-exit.v1'


def path(program):return Path(program)/'.helene/exit-request.json'


def profile_path(program,tree=None):
    if tree is None:
        from . import agents
        primary=agents.find(Path(program),agents.BASE_ID)
        if primary is None:return None
        tree=primary.tree
    identity=hashlib.sha256(os.path.normcase(str(Path(program).resolve())).encode('utf8')).hexdigest()
    return Path(tree)/'memory/.control'/('programme-exit-'+identity+'.json')


def profile_paths(program,tree=None):
    from . import agents
    trees=[row.tree for row in agents.roster(Path(program))]
    if tree is not None:trees.append(Path(tree))
    return list(dict.fromkeys(profile_path(program,root) for root in trees))


def record(program,descriptor):
    program=Path(program).resolve()
    if descriptor.get('program') not in (None,str(program)):raise ValueError('Exit belongs to another programme')
    value={'format':FORMAT,'program':str(program),'generation':descriptor.get('generation'),
        'pid':descriptor.get('pid',os.getpid()),'birth':descriptor.get('birth'),'at':time.time()}
    targets=[path(program)]
    try:
        targets.extend(profile_paths(program,descriptor.get('tree')))
    except (OSError,ValueError,TypeError):pass
    saved=False;errors=[]
    for marker in targets:
        try:write(marker,value);saved=True
        except OSError as exc:errors.append(exc)
    if not saved:raise RuntimeError('Cannot persist programme Exit intent') from errors[0]


def requested(program):
    # Presence, including an unreadable/malformed intent, blocks auto-restart.
    if path(program).exists():return True
    try:markers=profile_paths(program)
    except (OSError,ValueError,TypeError):return False
    return any(marker.exists() for marker in markers)


def matches(program,descriptor):
    """Close an observer only for the exact owner's durable terminal intent."""
    program=Path(program).resolve();targets=[path(program)]
    if descriptor.get('tree'):
        targets.append(profile_path(program,descriptor['tree']))
    for marker in targets:
        value=read(marker)
        if (value.get('format')==FORMAT and value.get('program')==str(program)
                and all(value.get(key)==descriptor.get(key) and descriptor.get(key) is not None
                        for key in ('pid','birth','generation'))):return True
    return False


def clear_for_manual_start(program):
    path(program).unlink(missing_ok=True)
    for marker in profile_paths(program):marker.unlink(missing_ok=True)
