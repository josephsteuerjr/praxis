"""Ownership metadata for product notices; archived/authored text is never inferred."""
from __future__ import annotations
import math
from . import ui_language

FIELDS={
    'speech_failed':(), 'speech_unknown':(), 'turn_incomplete':(),
    'files_pending':('count','run'), 'paused':('run',), 'turn_failed':('run',),
    'turn_cut':('run',), 'run_in_doubt':('run',), 'run_stopped':('run',),
    'silence':('agent',), 'silence_reason':('agent','reason'),
    'closed':(), 'closed_file':(), 'closed_unspoken':(), 'tree_busy':('agent','pid'),
    'birth_silent':('agent',), 'birth_failed':('tries',),
    'resume_refused':('reason',), 'note_gave_up':('name','tries','path'),
    'note_rejected':('name','reason'),
}


def validate(value):
    if (type(value) is not dict or set(value)!={'schema','kind','values'}
            or value.get('schema')!='helene.product-notice.v1'
            or type(value.get('kind')) is not str or value['kind'] not in FIELDS
            or type(value.get('values')) is not dict
            or set(value['values'])!=set(FIELDS[value['kind']])):
        raise ValueError('Invalid product notice ownership')
    values=value['values']
    if any(type(item) not in (str,int,float) or type(item) is float and not math.isfinite(item) for item in values.values()):
        raise ValueError('Invalid product notice value')
    return dict(value,values=dict(values))


def recipe(kind,**values):
    return validate({'schema':'helene.product-notice.v1','kind':kind,'values':values})


def legacy(value):
    value=validate(value)
    return ui_language.text('notice.'+value['kind'],'ru',value['values'])


def deliver(desk,code,*,source_id='',kind='',**values):
    owned=recipe(code,**values)
    return desk.deliver(legacy(owned),source_id=source_id,system=True,kind=kind,display_notice=owned)
