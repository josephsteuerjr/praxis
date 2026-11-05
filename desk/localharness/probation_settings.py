"""A bounded mutable settings surface after an exact portable handoff.

The original payload never changes. Private original documents must hash to
both prelaunch snapshots; unknown properties and executable/data bindings stay
frozen. A Save receipt alone supplies no approval or health credit.
"""
from pathlib import Path
import copy,hashlib,json,os,re,time

FORMAT='helene-probation-settings.v1'

# Existing desktop Settings fields only. Unknown root AND nested keys remain
# in the frozen projection. No import/runtime/data/installation selector occurs
# here; provider credentials and ordinary preferences are not programme code.
MODEL=('framework','provider','provider_id','api_mode','base_url','model','key','keys','max_tokens',
       'reasoning_effort','service_tier','fallback_model','fallback_framework','fallback_base_url','fallback_key',
       'fallback_api_mode','fallback_provider_id','vision_model','pinned','temperature')
MUTABLE={
    'agent':('name',),'owner':('name','room'),'model':MODEL,
    'phone':('enabled','mode','external','local_port'),'update':('url',),
    'backup':('enabled','every_days','keep','dir'),
    'relay':('enabled','port','instructions'),'images':('enabled','model','quality','size','background','provider','base_url','key'),
    'telegram':('bot_token','owner_id','mode','api_id','api_hash','phone','status_message','allow_from','allowed_ids','history_initial_limit',
                'proxy'),
    'sandbox':('enabled','network','mounts','mounts_denied'),
    'service':('session0','firewall'),'computer':('enabled','scopes','port'),
    'voice':('enabled','model','language','threads','keep_loaded','compute_type','beam_size',
             'tts_enabled','tts_provider','tts_model','tts_voice','tts_speed','tts_reference','tts_language'),
    'owner_context':('messages','characters','tokens'),
    'self_review':('after_update','after_self_edit','depth','models','max_parallel','max_iters'),
}

def _scalar(field,value):
    if value is None:return True
    if field in ('enabled','network','status_message','keep_loaded','pinned','session0','firewall','tts_enabled'):
        return type(value) is bool
    if field in ('owner_id','api_id'):return type(value) in (str,int)
    if field in ('port','local_port'):return type(value) is int and 1<=value<=65535
    if field in ('every_days','keep','max_tokens','history_initial_limit','threads','beam_size','messages','characters','tokens'):
        return type(value) is int
    if field in ('temperature','tts_speed'):return type(value) in (int,float)
    return type(value) is str

def _mount_rows(value,*,denied=False):
    if not isinstance(value,list):return False
    allowed={'path','why','at'} if denied else {'path','access','write','why','at'}
    for row in value:
        if isinstance(row,str):
            if not row.strip():return False
            continue
        if (not isinstance(row,dict) or not row.keys()<=allowed or not isinstance(row.get('path'),str)
                or not row['path'].strip()):return False
        if any(key in row and row[key] is not None and not isinstance(row[key],str) for key in ('why','at')):return False
        if 'access' in row and type(row['access']) not in (str,bool):return False
        if 'write' in row and type(row['write']) is not bool:return False
    return True

def _data_leaf(root,field,value):
    """Only these documented data collections are mutable as a whole.

    Provider keys: string->string credentials. IDs/scopes/model names: typed
    scalar lists. Mounts: legacy path strings or known path/access/write/why/at
    records; any unknown record option freezes the complete collection.
    """
    if value is None:return True
    if (root,field)==('model','keys'):
        return isinstance(value,dict) and all(isinstance(k,str) and isinstance(v,str) for k,v in value.items())
    if (root,field)==('telegram','allowed_ids'):
        return isinstance(value,list) and all(type(item) in (str,int) for item in value)
    if (root,field) in (('computer','scopes'),('self_review','models')):
        return isinstance(value,list) and all(isinstance(item,str) for item in value)
    if root=='sandbox' and field in ('mounts','mounts_denied'):return _mount_rows(value,denied=field=='mounts_denied')
    return False

DATA_LEAVES={('model','keys'),('telegram','allowed_ids'),('computer','scopes'),
             ('self_review','models'),('sandbox','mounts'),('sandbox','mounts_denied')}

def _hash(raw):return hashlib.sha256(raw).hexdigest()

def _document(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('Duplicate probation settings property')
            result[key]=value
        return result
    for encoding in ('utf-8-sig','utf-16','cp1251'):
        try:text=raw.decode(encoding)
        except UnicodeDecodeError:continue
        def invalid_constant(_value):raise ValueError('Nonfinite JSON settings number')
        value=json.loads(text,object_pairs_hook=pairs,parse_constant=invalid_constant)
        if not isinstance(value,dict):raise ValueError('Probation settings must be a JSON object')
        return value
    raise ValueError('Probation settings encoding is not supported')

def _encoded(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')

def frozen(value):
    result=copy.deepcopy(value)
    for key,fields in MUTABLE.items():
        if key not in result:continue
        item=result[key]
        # A legacy optional null is the unset/default surface, not an object
        # whose unknown options may be discarded. Other invalid types refuse.
        if item is None:result.pop(key);continue
        if key=='self_review':
            # This validator preserves unknown keys; only the explicit six
            # names above may be removed from the frozen projection.
            from .self_review import settings
            settings({'self_review':item})
        if not isinstance(item,dict):continue
        for field in fields:
            if field not in item:continue
            value=item[field]
            if (key,field)==('telegram','proxy') and isinstance(value,dict):
                # Proxy is an option object, not an opaque data map.
                remaining={name:child for name,child in value.items()
                           if name not in ('enabled','url','key') or not _scalar(name,child)}
                if remaining:item[field]=remaining
                else:item.pop(field)
                continue
            if (key,field) in DATA_LEAVES:
                if _data_leaf(key,field,value):item.pop(field)
            elif key=='self_review':item.pop(field)  # Existing settings() validated exact types/ranges.
            elif _scalar(field,value):item.pop(field)
        if not item:result.pop(key)
    if result.get('agent_mode') in ('sandbox','interactive'):result.pop('agent_mode')
    port=result.get('port')
    if port is None or (type(port) is int and 1<=port<=65535):result.pop('port',None)
    return _encoded(result)

def _names(program):
    from .essential_backup import configs
    return {path.relative_to(program).as_posix():path for path in configs(program) if path.is_file()}

def _raw(path):
    from .first_start import plain
    from .validation_read import checkpoint
    # Normal writers atomically replace one config. Observe a whole old or new
    # generation; never block cooperative Stop behind the writer's kernel lock.
    # Retry a concurrent replacement without treating an ordinary Save as code
    # mutation. Persistent churn still refuses cleanup instead of blessing it.
    stamp=lambda info:(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
    for attempt in range(8):
        checkpoint();path=plain(path)
        with path.open('rb') as source:
            before=os.fstat(source.fileno());chunks=[]
            while True:
                checkpoint();block=source.read(1024*1024);checkpoint()
                if not block:break
                chunks.append(block)
            after=os.fstat(source.fileno())
        live=path.stat();checkpoint()
        if stamp(before)==stamp(after) and stamp(after)==stamp(live):return b''.join(chunks)
        checkpoint();time.sleep(.005)
    raise ValueError('Probation settings kept changing during validation')

def _baseline(program,migration,digest):
    from .program_state import _identity
    from .first_start import plain
    _identity(migration)
    if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):
        raise ValueError('Invalid original probation settings digest')
    return plain(program/'.helene/probation-settings'/migration/(digest+'.json'))

def capture(program,expected,migration):
    """Called only at the existing fully validated prelaunch owner admission."""
    from .first_start import plain
    from .vision_policy import _write
    program=plain(program);paths=_names(program)
    if not isinstance(expected,dict) or paths.keys()!=expected.keys():
        raise ValueError('Prepared probation settings roster changed')
    documents={}
    for name,path in paths.items():
        raw=_raw(path)
        if _hash(raw)!=expected[name]:raise ValueError('Prelaunch probation settings changed: '+name)
        frozen(_document(raw))  # Validate semantics before any private write.
        documents[name]=raw
    for name,raw in documents.items():
        target=_baseline(program,migration,expected[name])
        if target.exists():
            if _hash(_raw(target))!=expected[name]:raise ValueError('Original probation settings copy changed')
        else:
            target.parent.mkdir(parents=True,exist_ok=True);plain(target.parent)
            _write(target,raw,private=True)
        if _hash(_raw(target))!=expected[name]:raise ValueError('Original probation settings copy is unconfirmed')
    return {'format':FORMAT,'program':str(program),'migration':migration,'settings':dict(expected)}

def _identity(program,proof,expected,migration=None):
    from .first_start import plain
    program=plain(program)
    if (not isinstance(expected,dict) or not isinstance(proof,dict) or proof.get('format')!=FORMAT or proof.get('program')!=str(program)
            or proof.get('settings')!=expected or (migration is not None and proof.get('migration')!=migration)):
        raise ValueError('Probation settings admission differs')
    if not isinstance(proof.get('migration'),str):raise ValueError('Probation settings migration identity is missing')
    from .program_state import _identity as operation
    operation(proof.get('migration'))
    return program

def _started(program,proof):
    from .package_notice import read
    from . import session_host
    note=read(program/'python-migration.json')
    owner=read(session_host.control_path(program))
    return (note.get('id')==proof['migration'] and note.get('new_root')==str(program)
            and note.get('state') in ('new_app_started','retirement_incomplete','completed')
            and owner.get('program')==str(program) and owner.get('state')=='running'
            and session_host.process_state(owner)=='alive')

def compare(program,proof,expected,*,migration=None):
    """Validate current documents, then retain the ORIGINAL comparison map."""
    program=_identity(program,proof,expected,migration)
    paths=_names(program)
    if paths.keys()!=expected.keys():raise ValueError('Probation settings roster changed')
    started=None
    for name,path in paths.items():
        original=_raw(_baseline(program,proof['migration'],expected[name]))
        if _hash(original)!=expected[name]:raise ValueError('Original probation settings copy changed')
        before=_document(original);current=_raw(path)
        if _hash(current)==expected[name]:continue
        if frozen(_document(current))!=frozen(before):
            raise ValueError('Probation settings changed a frozen programme/data/runtime binding: '+name)
        if started is None:started=_started(program,proof)
        if not started:raise ValueError('Settings changed before actual portable handoff: '+name)
    return dict(expected)

def paired(program,journal):
    proof=journal.get('trial_settings')
    if proof is None:return None
    prepared=(journal.get('completion') or {}).get('prepared') or {}
    expected=(journal.get('payload') or {}).get('settings')
    if (journal.get('mode')!='update' or prepared.get('trial_settings')!=proof
            or (prepared.get('payload') or {}).get('settings')!=expected
            or (prepared.get('note') or {}).get('id')!=proof.get('migration')):
        raise ValueError('Paired probation settings custody differs')
    _identity(program,proof,expected)
    return proof

def working(program,current):
    """Only the admitted mutable part is excluded from generation identity."""
    from .package_notice import read
    from . import portable_retirement as retirement
    from .first_start import receipt
    journal=read(retirement.path(program));proof=paired(program,journal)
    if proof is None:return current
    if read(receipt(program)).get('retirement')!={'id':journal.get('id'),'sha256':retirement.authority(journal)}:
        raise ValueError('Working probation settings seal differs')
    result=dict(current)
    result['settings']=compare(program,proof,journal['payload']['settings'])
    return result

def prepared(program,value):
    from .package_notice import read
    from . import portable_retirement as retirement
    from .first_start import receipt
    journal=read(retirement.path(program));proof=paired(program,journal)
    admitted={'id':journal.get('id'),'sha256':retirement.authority(journal)}
    if (proof!=value.get('trial_settings') or value.get('retirement')!=admitted
            or read(receipt(program)).get('retirement')!=admitted
            or (journal.get('payload') or {}).get('settings')!=value['payload']['settings']):
        raise ValueError('Prepared probation settings are not paired with sealed retirement custody')
    return compare(program,proof,value['payload']['settings'],migration=value['note']['id'])
