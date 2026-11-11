"""One model/Python edit interface; explicit home or Forge task address."""
from __future__ import annotations
import json
import re
import edit_batch
import hands

DESCRIPTION = (
    'YOUR EDITING HAND. Without task_id, paths are in your home: workspace/soul/memory; '
    'core edits need proposal_id. With task_id, paths and execution belong to that Forge task. '
    'replace: old occurs exactly once, or all=true replaces all non-overlapping matches; write: full content (home overwrite/force are explicit); '
    'patch: unified diff in a Forge task, git apply --check then apply, no per-file SHA guarantee; '
    'batch: versioned replacements. Pass expected_sha256 from fs_read; a confirmed edit after_sha256 is valid for the next edit. '
    'Reply is edit: JSON, status/message/before_sha256/after_sha256 and files for a batch. '
    'confirmed means written and read back; refused means untouched; in_doubt means outcome unknown: '
    'reread, NEVER retry blind. Patch success is ok, not readback-confirmed. '
    'Remote success is ok (backend-reported, no local readback promise); ambiguous errors/lost replies are in_doubt. '
    'Shell does not hold the shared mutation lock.' + edit_batch.DESCRIPTION)


def schema(worker=False):
    props = {
        'action': {'type':'string','enum':['replace','write','patch','batch'],'description':'Editing operation.'},
        'path': {'type':'string','description':'Path relative to the home or task root.'},
        'content': {'type':'string','description':'write: full UTF-8 content.'},
        'old': {'type':'string','description':'replace: nonempty exact text; unique unless all=true.'},
        'all': {'type':'boolean','description':'replace: replace every occurrence; overlapping matches still refuse. Reply includes replacements count.'},
        'new': {'type':'string','description':'replace: replacement text, empty deletes.'},
        'patch': {'type':'string','description':'patch: unified diff; requires a Forge task.'},
        'expected_sha256': {'type':'string','description':'Read version for replace/write; batch carries each file version.'},
        'files': edit_batch.FILES_SCHEMA, 'mode': edit_batch.MODE_SCHEMA,
    }
    if not worker:
        props.update({
            'task_id': {'type':'string','description':'Optional Forge task; omitted edits your home.'},
            'proposal_id': {'type':'string','description':'Home edits: proposal worktree for core files; incompatible with task_id.'},
            'overwrite': {'type':'boolean','description':'Home write: explicitly overwrite an existing file.'},
            'force': {'type':'boolean','description':'Home write: explicitly allow a large shrink.'},
        })
    return {'type':'object','properties':props,'required':['action']}


def refusal(message, files=None):
    return edit_batch.refused(files,message) if files is not None else hands.outcome('refused',message)


def remote_result(value):
    """Preserve backend evidence; never infer 'untouched' from an unfamiliar string."""
    if not isinstance(value,dict):
        return hands.outcome('in_doubt','Remote edit returned no structured outcome; reread before retry.')
    terminal=value.get('type') not in {'accepted','progress','cancelled'}
    if value.get('ok') is True and terminal and not value.get('after_error'):
        status='ok'
    elif (value.get('code') in {'unavailable','unsupported'}
          or (terminal and not value.get('after_error')
              and value.get('ok') is False and value.get('stage')=='preflight')):
        status='refused'
    else:
        status='in_doubt'
    message=str(value.get('error') or value.get('text') or
                ('Remote executor reports success; no local readback guarantee.' if status=='ok'
                 else 'Remote outcome is unknown; reread before retry.'))
    result=hands.outcome(status,message)
    result.update(backend_result=value)
    if status=='ok' and type(value.get('replacements')) is int and value['replacements']>=0:
        result['replacements']=value['replacements']
    for source,target in [('before','before_sha256'),('after','after_sha256')]:
        digest=value.get(source)
        if isinstance(digest,str) and re.fullmatch('[0-9a-fA-F]{16}|[0-9a-fA-F]{64}',digest):result[target]=digest
    return result


def edit_result(action='replace', path='', content='', old='', new='', patch='', expected_sha256='',
                files=None, mode='snapshot', task_id='', proposal_id='', overwrite=False, force=False,
                _parent_request_path='', _script_context=None, all=False):
    import workshop
    action=str(action or '').strip().lower()
    if action not in {'replace','write','patch','batch'}:
        return refusal('action must be replace, write, patch or batch.',files)
    if not isinstance(all, bool) or (all and action!='replace'):
        return refusal('all must be a boolean and belongs to replace; batch puts all in each edit.',files)
    if action=='batch' and files is None:
        return refusal('batch requires files.',[])
    if action!='batch' and files is not None:
        return refusal('files requires action=batch.',files)
    if action=='batch' and (path or content or old or new or patch or expected_sha256):
        return refusal('batch uses files and mode, without single-file arguments.',files)
    if action!='batch' and mode!='snapshot':
        return refusal('mode applies only to batch.',files)
    if task_id:
        if proposal_id or overwrite or force:
            return refusal('proposal_id/overwrite/force belong to home edits; Forge write is explicit full content.',files)
        import forge
        reply=forge.edit(task_id,action,path=path,content=content,old=old,new=new,patch=patch,
                         expected_sha256=expected_sha256,files=files,mode=mode,all=all,
                         _parent_request_path=_parent_request_path,_script_context=_script_context,_typed_result=True)
        if reply.startswith('remote_edit: '):
            return json.loads(reply[13:])
        if reply.startswith('batch: '):
            return json.loads(reply[7:])
        result=hands.outcome(forge._edit_outcome(reply),reply)
        match=re.search(r'; sha256 (\w+) → ([0-9a-f]{64});',reply)
        if match:result.update(before_sha256=None if match[1]=='new' else match[1],after_sha256=match[2])
        count = re.search(r'; replacements=(\d+)', reply)
        if count and result['status']=='confirmed':result['replacements']=int(count[1])
        return result
    if _script_context is not None or _parent_request_path:
        return refusal('A verified command/worker context requires its Forge task.',files)
    if action=='patch':
        return refusal('patch requires task_id. For home files use versioned replace/batch or open a Forge task.')
    if action=='write':
        return _receipted('write',path,workshop.fs_write_result(path,content,proposal_id,overwrite,force,expected_sha256))
    if action=='replace':
        return _receipted('edit',path,workshop.fs_edit_result(path,old,new,proposal_id,expected_sha256,all=all))
    return _receipted('edit','',workshop.fs_edit_result(proposal_id=proposal_id,files=files,mode=mode))


def _receipted(op,path,result):
    """Home edits leave the same trail as fs_write in memory/.state/hands.jsonl."""
    import workshop
    for row in result.get('files', [dict(result,path=path)]):
        hands.receipt(op,row['status']=='confirmed',row['path'],f"{row['status']}: {row['message']}",base=workshop.BASE)
    return result


def render(result):
    return 'edit: '+json.dumps(result,ensure_ascii=False)


def edit(**kwargs):
    import tool_report
    result=edit_result(**kwargs)
    target=str(kwargs.get('path') or ', '.join(str(r['path']) for r in result.get('files',[])))
    tool_report.note(outcome=result['status'],target=target)
    return render(result)
