"""Source-owned live machine captions; raw observations and old clients stay literal."""
from __future__ import annotations
import contextlib, json, math
from pathlib import Path
FIELDS=json.loads((Path(__file__).resolve().parents[1]/'resources/machine-display.json').read_text(encoding='utf8'))
SCHEMA='helene.machine-display.v1'
def _valid(value,depth=0):
    if depth>8:return False
    if type(value) is str:return True
    if type(value) in (int,float):return math.isfinite(value)
    if type(value) is not dict or set(value)!={'schema','key','values'}:return False
    key=value.get('key');values=value.get('values')
    if value.get('schema')!=SCHEMA or type(key) is not str or key not in FIELDS or type(values) is not dict or set(values)!=set(FIELDS[key]):return False
    if key=='join':return type(values['parts']) is list and len(values['parts'])<=32 and all(_valid(v,depth+1) for v in values['parts'])
    return all(_valid(v,depth+1) for v in values.values())
def make(key,**values):
    value={'schema':SCHEMA,'key':key,'values':values}
    if not _valid(value):raise ValueError('Invalid machine display recipe')
    return value
def join(*parts):return make('join',parts=[p for p in parts if p!=''])
def render(value,translate):
    if type(value) is not dict or not _valid(value):return ''
    def walk(node):
        if type(node) is not dict:return str(node)
        if node['key']=='join':return '; '.join(walk(v) for v in node['values']['parts'])
        return translate('machine.'+node['key'],**{k:walk(v) for k,v in node['values'].items()})
    return walk(value)
def frame(agent,value,legacy):
    language=getattr(agent,'frame_language',None)
    if getattr(agent,'FRAME_TEXT_BOUNDARIES_API',0)==1 and language:
        pin=getattr(language,'pin',None)
        with pin() if callable(pin) else contextlib.nullcontext():
            return render(value,language.text) or legacy
    return legacy
