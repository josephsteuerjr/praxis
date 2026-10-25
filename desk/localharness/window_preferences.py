"""Installation window storage: one transaction for manual and agent writers."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import re
from .config_transaction import transaction

APPEARANCE=frozenset({'helene.look','frame.theme','frame.rail','frame.panel','frame.rail.width','frame.panel.width','helene.zoom'})

def preference_key(key):
    return isinstance(key,str) and key.startswith(('frame.','helene-','helene.','settings.','draft.'))

def _load(root):
    try:value=json.loads((Path(root)/'window-preferences.json').read_text(encoding='utf-8'))
    except FileNotFoundError:return {}
    if not isinstance(value,dict):raise ValueError('Invalid window preferences')
    return value

def _state(values):
    selected={k:values[k] for k in sorted(APPEARANCE) if k in values}
    raw=json.dumps(selected,sort_keys=True,separators=(',',':'))
    return {'ok':True,'scope':'installation','values':selected,'seen':hashlib.sha256(raw.encode()).hexdigest(),'restart_needed':False}

def read(root):
    with transaction(Path(root)/'window-preferences.json'):return _state(_load(root))

def storage(root):
    with transaction(Path(root)/'window-preferences.json'):
        return {k:v for k,v in _load(root).items() if preference_key(k) and isinstance(v,str)}

def bootstrap(root):
    with transaction(Path(root)/'window-preferences.json'):
        values=_load(root)
        return {k:v for k,v in values.items() if preference_key(k) and isinstance(v,str)}, _state(values)['seen']

def validate(key,value):
    if not preference_key(key) or len(key)>512:raise ValueError('Invalid window preference key')
    if value is None:return
    if not isinstance(value,str) or len(value)>1024*1024:raise ValueError('Invalid preference value')
    if key=='frame.theme' and value not in ('system','light','dark'):raise ValueError('Unknown theme')
    if key in ('frame.rail','frame.panel') and value not in ('0','1'):raise ValueError('Invalid panel visibility')
    if key in ('frame.rail.width','frame.panel.width','helene.zoom'):
        number=float(value)
        lo,hi=(.7,2.2) if key=='helene.zoom' else (220,700)
        if not math.isfinite(number) or not lo<=number<=hi:raise ValueError('Geometry outside supported range')
    if key=='helene.look':
        look=json.loads(value)
        if not isinstance(look,dict):raise ValueError('Look must be an object')
        if 'uiScale' in look and (type(look['uiScale']) not in (int,float) or not math.isfinite(look['uiScale']) or not .7<=look['uiScale']<=1.5):raise ValueError('Scale must be 0.7..1.5')
        for k,choices in {'style':('paper','clean','studio','glass','terminal'),'density':('comfortable','compact'),'font':('hand','sans','serif','mono'),'shape':('default','round','square'),'backdrop':('plain','grid','glow')}.items():
            if k in look and look[k] not in choices:raise ValueError('Invalid '+k)
        for k in ('dayColor','nightColor','accentColor'):
            if k in look and (not isinstance(look[k],str) or (look[k] and not re.fullmatch('#[0-9a-fA-F]{6}',look[k]))):raise ValueError('Invalid '+k)

def change(root,patch,seen=None,*,appearance_only=False):
    if not isinstance(patch,dict) or not patch:raise ValueError('Nonempty preference patch required')
    for key,value in patch.items():
        validate(key,value)
        if appearance_only and key not in APPEARANCE:raise ValueError('Not an appearance key')
    path=Path(root)/'window-preferences.json'
    with transaction(path):
        values=_load(root);before=_state(values)
        if APPEARANCE.intersection(patch) and ((appearance_only and seen is None) or (seen is not None and seen!=before['seen'])):
            return dict(before,ok=False,code='conflict',error='Appearance changed; read and reapply')
        changed=[]
        for k,v in patch.items():
            if v is None:
                if k in values:del values[k];changed.append(k)
            elif values.get(k)!=v:values[k]=v;changed.append(k)
        if changed:
            path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_suffix('.json.tmp')
            try:temporary.write_text(json.dumps(values,ensure_ascii=False),encoding='utf-8');os.replace(temporary,path)
            finally:temporary.unlink(missing_ok=True)
        return dict(_state(values),changed=changed)

def reset(root,seen):return change(root,{k:None for k in APPEARANCE},seen,appearance_only=True)

def install(agent,root):
    root=Path(root).resolve()
    def handler(action='read',patch=None,seen=None):
        if agent._active_scope()!='owner':return json.dumps({'ok':False,'code':'owner_only'})
        try:
            if action=='read':result=read(root)
            elif action=='patch':
                if not isinstance(patch,list) or not 1<=len(patch)<=len(APPEARANCE):raise ValueError('patch is an array of key/value entries')
                changes={}
                for row in patch:
                    if not isinstance(row,dict) or set(row)!={'key','value'} or row['key'] not in APPEARANCE or row['key'] in changes:raise ValueError('Invalid or duplicate appearance entry')
                    changes[row['key']]=row['value']
                result=change(root,changes,seen,appearance_only=True)
            elif action=='reset':result=reset(root,seen)
            else:raise ValueError('action: read | patch | reset')
            return json.dumps(result,ensure_ascii=False)
        except (OSError,ValueError,TypeError) as exc:return json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False)
    agent.TOOL_IMPL['window_appearance']=handler
    schema={'name':'window_appearance','description':'Read or change this desktop installation appearance. Read first; patch/reset require seen. Patch is an array of {key,value} entries: include only keys to change; value is a string (helene.look is JSON) or explicit null to delete. Look supports uiScale 0.7..1.5, style paper/clean/studio/glass/terminal, font hand/sans/serif/mono, shape default/round/square, backdrop plain/grid/glow, density comfortable/compact, dayColor/nightColor/accentColor hex. frame.theme system/light/dark; frame.rail/panel 0 shown/1 collapsed; widths 220..700. Never changes agent identity or config.','input_schema':{'type':'object','properties':{'action':{'type':'string','enum':['read','patch','reset']},'patch':{'type':'array','minItems':1,'maxItems':7,'items':{'type':'object','properties':{'key':{'type':'string','enum':sorted(APPEARANCE)},'value':{'type':['string','null']}},'required':['key','value'],'additionalProperties':False}},'seen':{'type':'string'}},'required':['action']}}
    agent.OWNER_TOOLS[:]=[s for s in agent.OWNER_TOOLS if s.get('name')!='window_appearance']+[schema]
