"""One owner-consented desktop update request, shared by agent and window."""
from pathlib import Path
import os,re,time,uuid
from .package_notice import read,write
from .config_transaction import transaction

def path(tree):return Path(tree)/'memory/.control/desktop-update-request.json'

def program():
    config=Path(os.environ.get('HELENE_CONFIG') or '').resolve()
    base=config.parent.parent.parent if config.parent.parent.name=='agents' else config.parent
    if not (base/'python-app.json').is_file():raise ValueError('Desktop installation is unavailable in this runner')
    return base

def status(tree):return read(path(tree)) or {'state':'none'}

def request(tree,action,*,source_mode=None,version='latest',reason='',owner_words='',owner_spoke=False,root=None,by='agent',identity=''):
    from .python_updates import version as installed_version,parts
    if action=='status':return status(tree)
    target=Path(root).resolve() if root is not None else program()
    if source_mode is not None and source_mode not in ('preserve','clean'):raise ValueError('Expected preserve or clean source mode')
    with transaction(path(tree)):
        previous=status(tree)
        if action=='status':return previous
        if action=='plan':
            if previous.get('state') in ('confirmed','running','handed_off'):raise ValueError('An update is already admitted; inspect its receipt')
            if version!='latest':parts(version)
            if owner_words and not owner_spoke:raise ValueError('Only a direct owner turn can consent to an update')
            mode=source_mode or 'preserve';words=' '.join(str(owner_words).split())
            clean_consent=bool(re.search(r'clean|чист|без.{0,40}(?:локальн|прав)|(?:сброс|отброс).{0,40}(?:код|прав|измен)',words,re.I))
            admitted=bool(words and (mode!='clean' or clean_consent))
            value={'format':'helene-desktop-update-request.v1','id':uuid.uuid4().hex,'created':time.time(),
                'program':str(target),'tree':str(Path(tree).resolve()),'from_version':installed_version(target),
                'version':version,'source_mode':mode,'reason':str(reason),'owner_words':words,
                'confirmed_by':'owner-words' if admitted else '', 'state':'confirmed' if admitted else 'awaiting'}
        elif action in ('confirm','decline'):
            if previous.get('state')!='awaiting' or (identity and previous.get('id')!=identity):raise ValueError('No matching pending update proposal')
            if previous.get('program')!=str(target) or previous.get('from_version')!=installed_version(target):raise ValueError('Installation changed; request a new update')
            if by!='window' and (not owner_spoke or len(str(owner_words).strip())<2):raise ValueError('Direct owner confirmation is required')
            value={**previous,'state':'confirmed' if action=='confirm' else 'declined','confirmed_by':by,
                   'owner_words':str(owner_words),'confirmed_at':time.time()}
            if by=='window' and source_mode is not None:value['source_mode']=source_mode
        else:raise ValueError('Expected status, plan, confirm or decline')
        write(path(tree),value);return value

def consume(owner):
    """Claim before effects; an interrupted execution is never replayed."""
    from . import agents,owner_stop,python_updates as updater
    if owner.stop.is_set() or owner_stop.stopped():return
    for agent in agents.roster(owner.root):
        if not agent.enabled or agent.conflict:continue
        target=path(agent.tree)
        if not target.is_file():continue
        with transaction(target):
            value=status(agent.tree)
            if value.get('state')!='confirmed':continue
            if value.get('program')!=str(owner.root.resolve()) or value.get('tree')!=str(agent.tree.resolve()):continue
            if value.get('from_version')!=updater.version(owner.root) or time.time()-value['created']>7200:
                value.update(state='superseded',error='Installation or consent period changed');write(target,value);continue
            value.update(state='running',started=time.time());write(target,value)
        try:
            tag=value['version'];url='https://api.github.com/repos/josephsteuerjr/praxis/releases/'+('latest' if tag=='latest' else 'tags/v'+tag.lstrip('v'))
            release=updater.update_check(owner,url)
            if not release['newer']:raise ValueError('Requested release is not newer than the installed version')
            downloaded=updater.update_download(owner,release['url'],release['sha256'])
            if owner.stop.is_set() or owner_stop.stopped():raise ValueError('Owner stopped before update handoff')
            result=updater.update_install(owner,downloaded['path'],sourceMode=value['source_mode'])
            value.update(state='handed_off',result=result)
        except Exception as exc:value.update(state='failed',error=f'{type(exc).__name__}: {exc}')
        write(target,value)
        owner.event('desktop-update-request',{'id':value['id'],'state':value['state'],'source_mode':value['source_mode']})

def watch(owner,stop):
    while not stop.wait(.5):
        try:consume(owner)
        except Exception as exc:owner.event('desktop-update-request-error',{'error':str(exc)})
