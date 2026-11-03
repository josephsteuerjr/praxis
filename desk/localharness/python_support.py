"""Explicit diagnostic sources, bounded tails, credential-masked support ZIP."""
import json,os
from pathlib import Path
import re
import tempfile
import time
import zipfile
from .essential_backup import load


def redactor(configuration):
    secrets=[]
    def visit(value,key=''):
        if isinstance(value,dict):
            for k,v in value.items():visit(v,k)
        elif isinstance(value,list):
            for v in value:visit(v,key)
        elif isinstance(value,str) and len(value)>3 and re.search('key|token|password|secret|api_hash',key,re.I):secrets.append(value)
    visit(configuration)
    def mask(text):
        for value in sorted(secrets,key=len,reverse=True):text=text.replace(value,'[redacted]')
        text=re.sub(r'(?i)(bearer\s+)[^\s\"<>]+',r'\1[redacted]',text)
        text=re.sub(r'(?i)([?&](?:key|token|password)=)[^&\s\"<>]+',r'\1[redacted]',text)
        text=re.sub(r'(?i)((?:api[_-]?key|access_token|refresh_token|password)\s*[\"\s:=]+)[^\s\",}]+',r'\1[redacted]',text)
        return text
    return mask


def bundle(root,agent):
    cfg=load(agent.config);mask=redactor(cfg)
    destination=Path(tempfile.gettempdir())/('helene-support-'+str(time.time_ns())+'.zip')
    sources=[agent.tree/p for p in ('runner.log','deskapp.log','xtts.log','voice-download.log','component-download.log','memory/.state/operation-errors.json')]
    sources+=sorted((agent.tree/'relay/logs').glob('*.log'))[-3:]
    sources+=sorted((Path(root)/'.helene/logs').glob('*.log'))[-3:]
    from .first_start import Places,plain
    from .startup_diagnostics import location,owner_location
    sources.extend((location(agent.tree),owner_location(agent.tree)))
    if os.name=='nt':sources.extend((location(Places.current().data),owner_location(Places.current().data)))
    sources=list(dict.fromkeys(sources))
    def selected(path,keys):
        try:value=load(plain(path))
        except (OSError,ValueError,TypeError):return {}
        return {key:value[key] for key in keys if key in value}
    session=selected(agent.tree/'memory/.control/desktop-session.json',('state','pid','birth','program'))
    reader=selected(agent.tree/'memory/.control/desk_inbox/.reader.json',('pid','at','autonomy'))
    if isinstance(reader.get('autonomy'),dict):
        reader['autonomy']={key:reader['autonomy'][key] for key in ('paused','stopped') if key in reader['autonomy']}
    from . import owner_stop
    stops=[]
    for kind,path in (('machine',owner_stop.seed_file()),('user',owner_stop.client_file()),('legacy-user',owner_stop.legacy_client_file())):
        if path is None:continue
        row={'kind':kind,'path':str(path)}
        try:
            info=Path(path).stat();row.update(present=True,modified_at=info.st_mtime)
        except FileNotFoundError:row['present']=False
        except OSError as error:row.update(present=True,read_error=type(error).__name__)
        stops.append(row)
    context={'format':'helene-support-runtime.v1','created_at':time.time(),'program':str(root),
             'stops':stops,
             'data':str(agent.tree),'session':session,'reader':reader,'sources':[]}
    context['build']=selected(Path(root)/'python-app.json',('version','target','delivery'))
    context['retirement']=selected(Path(root)/'.helene/retirement.json',('state','mode','error','removed','owner'))
    context['components']={name:selected(Path(root)/'.helene/component-jobs'/(name+'.json'),
        ('state','stage','error','error_type','started_at','updated_at','bytes','total_bytes','files_done','total_files'))
        for name in ('xtts','whisper')}
    try:
        from .desktop_update_request import status
        update=status(agent.tree)
        context['update']={key:update[key] for key in ('state','phase','error','from_version','to_version','source_mode') if key in update}
    except (OSError,ValueError,TypeError,KeyError):pass
    with zipfile.ZipFile(destination,'x',zipfile.ZIP_DEFLATED) as output:
        for i,path in enumerate(sources):
            if path.is_file():
                try:plain(path)
                except ValueError:continue
                with path.open('rb') as reader:
                    reader.seek(max(0,path.stat().st_size-262144));raw=reader.read(262144)
                output.writestr(str(i)+'-'+path.name,mask(raw.decode('utf-8',errors='replace')))
                context['sources'].append({'name':path.name,'modified_at':path.stat().st_mtime})
        output.writestr('runtime-status.json',mask(json.dumps(context,ensure_ascii=False)))
        output.writestr('scope.txt','Bounded diagnostic log tails, command failures and selected runtime status. Configuration, session credentials and source archives are excluded.')
    return destination
