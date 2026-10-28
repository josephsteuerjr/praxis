"""Explicit diagnostic sources, bounded tails, credential-masked support ZIP."""
import json
from pathlib import Path
import re
import tempfile
import time
import zipfile
from .essential_backup import load


def bundle(root,agent):
    cfg=load(agent.config)
    secrets=[]
    def visit(value,key=''):
        if isinstance(value,dict):
            for k,v in value.items():visit(v,k)
        elif isinstance(value,list):
            for v in value:visit(v,key)
        elif isinstance(value,str) and len(value)>3 and re.search('key|token|password|secret|api_hash',key,re.I):secrets.append(value)
    visit(cfg)
    def mask(text):
        for value in sorted(secrets,key=len,reverse=True):text=text.replace(value,'[redacted]')
        text=re.sub(r'(?i)(bearer\s+)[^\s\"<>]+',r'\1[redacted]',text)
        text=re.sub(r'(?i)([?&](?:key|token|password)=)[^&\s\"<>]+',r'\1[redacted]',text)
        text=re.sub(r'(?i)((?:api[_-]?key|access_token|refresh_token|password)\s*[\"\s:=]+)[^\s\",}]+',r'\1[redacted]',text)
        return text
    destination=Path(tempfile.gettempdir())/('helene-support-'+str(time.time_ns())+'.zip')
    sources=[agent.tree/p for p in ('runner.log','deskapp.log','xtts.log')]
    sources+=sorted((agent.tree/'relay/logs').glob('*.log'))[-3:]
    with zipfile.ZipFile(destination,'x',zipfile.ZIP_DEFLATED) as output:
        for i,path in enumerate(sources):
            if path.is_file():
                with path.open('rb') as reader:
                    reader.seek(max(0,path.stat().st_size-262144));raw=reader.read(262144)
                output.writestr(str(i)+'-'+path.name,mask(raw.decode('utf-8',errors='replace')))
        output.writestr('scope.txt','Named diagnostic log tails only. No configuration, memory, sessions or source archive.')
    return destination
