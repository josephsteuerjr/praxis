"""Optional payload installation belongs to the session's child Job, never a UI."""
from pathlib import Path
import argparse,os,sys,time,uuid
if not __package__:
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]));__package__='localharness'
from .package_notice import read
from .first_start import _write

NAMES={'xtts','whisper'}


def path(program,name):
    if name not in NAMES:raise ValueError('Unknown optional component')
    return Path(program)/'.helene/component-jobs'/(name+'.json')


def identity(pid=None):
    import psutil
    process=psutil.Process(os.getpid() if pid is None else pid)
    return {'pid':process.pid,'birth':process.create_time()}


def alive(pin):
    import psutil
    if not isinstance(pin,dict) or not isinstance(pin.get('birth'),(float,int)):return None
    try:
        process=psutil.Process(pin['pid'])
        return process.create_time()==pin['birth'] and process.status() not in (psutil.STATUS_ZOMBIE,psutil.STATUS_DEAD)
    except psutil.NoSuchProcess:return False
    except (psutil.Error,KeyError,TypeError):return None


def status(program,name):
    value=read(path(program,name))
    if not value:return {}
    row=dict(value);row['active']=False
    if row.get('state')=='running':
        state=alive(row.get('worker') or row.get('producer'))
        if state is True:row['active']=True
        elif state is False:
            row.update(state='interrupted',error='Установка прервана. Нажми «Повторить»; проверенный кеш сохранён.')
        else:row.update(state='attention',error='Состояние установщика не подтверждено; повторный запуск запрещён.')
    return row


def update(program,name,operation,**changes):
    from .config_transaction import transaction
    target=path(program,name)
    with transaction(target.with_suffix('.write')):
        value=read(target)
        if value.get('id')!=operation:raise RuntimeError('Component installation was replaced')
        value.update(changes,updated_at=time.time());_write(target,value,private=True)
    return value


def claim(program,name,tree):
    from .config_transaction import transaction
    target=path(program,name)
    with transaction(target.with_suffix('.write')):
        current=status(program,name)
        if current.get('active') or current.get('state')=='attention':
            raise RuntimeError('Этот компонент уже устанавливается; дождись завершения.')
        value={'format':'helene-component-job.v1','id':uuid.uuid4().hex,'component':name,
               'program':str(Path(program).resolve()),'tree':str(Path(tree).resolve()),
               'producer':identity(),'state':'running','stage':'starting','started_at':time.time()}
        _write(target,value,private=True)
    return value


def execute(program,name,tree,operation,progress=None):
    from . import component_downloads
    update(program,name,operation,worker=identity(),stage='preparing')
    def report(value):
        selected={k:value[k] for k in ('stage','asset','bytes','total_bytes','files','total_files','files_done') if k in value}
        update(program,name,operation,**selected)
        if progress:progress(value)
    try:
        result=component_downloads.install(program,[name],report,tree=tree)
        update(program,name,operation,state='completed',stage='completed',error='',finished_at=time.time())
        return result
    except BaseException as error:
        update(program,name,operation,state='failed',stage='failed',error=str(error),error_type=type(error).__name__)
        raise


def inline(program,name,tree,progress=None):
    value=claim(program,name,tree)
    return execute(program,name,tree,value['id'],progress)


def start(owner,name):
    from . import component_downloads
    from .update_process import interpreter_for,permanent_environment
    with owner.lock:
        if owner.exiting or owner.stop.is_set():raise RuntimeError('Programme Exit is already in progress')
        component_downloads.select(component_downloads.load(owner.root),[name])
        children=getattr(owner,'component_jobs',{})
        previous=children.get(name)
        if previous is not None and previous.handle is not None:
            if previous.poll() is None:return status(owner.root,name)
            previous.stop()  # Confirm the whole previous Job, not only its leader.
        tree=owner.current().tree;value=claim(owner.root,name,tree)
        env=permanent_environment();env.update(PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
        argv=[str(interpreter_for(owner.root)),'-B','-s',str(owner.root/'app/localharness/component_install_job.py'),
              '--program',str(owner.root),'--tree',str(tree),'--component',name,'--operation',value['id']]
        child=None
        try:
            child=owner.background(argv,tree/'component-download.log',env)
            children[name]=child;owner.component_jobs=children
            try:pin=identity(child.pid)
            except Exception:
                if not read(path(owner.root,name)).get('state') in ('completed','failed'):
                    raise RuntimeError('Установщик завершился до подтверждения запуска; подробности в журнале компонентов.')
            else:update(owner.root,name,value['id'],worker=pin)
        except BaseException as error:
            if child is not None and child.handle is not None:
                try:child.stop()
                except (OSError,RuntimeError) as custody_error:
                    child.custody_uncertain=True;owner.error=str(custody_error)
                    raise RuntimeError('Не удалось подтвердить остановку установщика; повторный запуск запрещён') from custody_error
            update(owner.root,name,value['id'],state='failed',stage='failed',error=str(error),error_type=type(error).__name__)
            raise
        return status(owner.root,name)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--program',type=Path,required=True);parser.add_argument('--tree',type=Path,required=True)
    parser.add_argument('--component',choices=sorted(NAMES),required=True);parser.add_argument('--operation',required=True)
    args=parser.parse_args();execute(args.program,args.component,args.tree,args.operation)
