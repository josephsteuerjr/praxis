"""Retire captured old-programme update metadata; never accept its evidence.

The replacement transaction supplies validated programme/data scopes. Capture
does not stop a producer or write work. Retire observes only captured PID/birth
identities, rechecks the exact records under their writer locks and leaves new,
unrelated, live or uncertain work alone. Callers seal the returned plan in their
own replacement authority before using it.

Typed trusted task receipts cover only their recorded unit supervisors. They
are not custody proof for arbitrary mutated adapters or untyped command units;
unsupported evidence blocks retirement. Whole-programme stop/Job custody is a
separate responsibility of the replacement backend.
"""
from __future__ import annotations

import copy
from contextlib import nullcontext
import hashlib
import json
import math
from pathlib import Path
import re
import time

from .package_notice import read, write
from .config_transaction import transaction

FORMAT = 'helene-update-supersession.v1'
KINDS = {'migration', 'source-update', 'install', 'download'}
TERMINAL = {'completed', 'cancelled', 'declined', 'superseded', 'applied'}
REASON = 'Programme replaced; prior update work superseded without accepting tests or review'
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
_SOURCE_ID = re.compile(r'[0-9a-f]{64}\Z')
_FIELDS = ('admission', 'execution_owner', 'helper', 'producer', 'process_identity')
_UNIT_TERMINAL = {'done', 'error', 'cancelled', 'stopped', 'failed', 'timed_out'}


def _plain(value):
    from .first_start import plain
    if not isinstance(value, (str, Path)) or not str(value).strip() or not Path(value).is_absolute():
        raise ValueError('Supersession scope must be an absolute path')
    return plain(value)


def _same_path(value, expected):
    try:return _plain(value) == _plain(expected)
    except (OSError, ValueError, TypeError):return False


def _identity(value, protocol):
    result = {key:value.get(key) for key in ('id', 'program', 'tree')}
    if protocol == 'desktop':result['kind'] = value.get('kind')
    else:
        result.update(directory=value.get('directory'), input_sha256=value.get('input_sha256'))
    return result


def _producer(value):
    if not isinstance(value, dict):return None
    pid, birth = value.get('pid'), value.get('birth')
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:return None
    if isinstance(birth, bool) or not isinstance(birth, (int, float)) or not math.isfinite(birth) or birth <= 0:return None
    return {key:copy.deepcopy(value[key]) for key in ('pid', 'birth', 'generation', 'invocation', 'adapter') if key in value}


def _topology(value):
    """Producer slots, not mutable progress or test/review verdicts."""
    result = {'identities':{field:(_producer(value[field]) or copy.deepcopy(value[field]))
        for field in _FIELDS if value.get(field)}}
    identity = _producer(value)
    if identity is not None:result['process'] = identity
    references = ('task_id', 'unit_id', 'review_id', 'subject_sha256')
    for field in ('staging', *references, 'stop_confirmed'):
        if field in value:result[field] = copy.deepcopy(value[field])
    for field in ('attempts', 'agent_review_rounds'):
        if field not in value:continue
        rows = value[field]
        result[field] = ([{key:copy.deepcopy(row[key]) for key in references if key in row}
                          if isinstance(row, dict) else copy.deepcopy(row) for row in rows]
                         if isinstance(rows, list) else copy.deepcopy(rows))
    coordinator = value.get('coordinator')
    if coordinator:
        result['coordinator'] = ({key:copy.deepcopy(coordinator[key]) for key in ('review_store','job_control') if key in coordinator}
                                 if isinstance(coordinator, dict) else copy.deepcopy(coordinator))
    if 'plans' in value:result['plans'] = copy.deepcopy(value['plans'])
    return result


def _producers(value, *, document, directory=None):
    found, problems = [], []
    documents = {str(document):_topology(value)}
    for field in _FIELDS:
        raw = value.get(field)
        if not raw:continue
        # Forge's string admission is not a PID receipt.
        if field == 'admission' and not isinstance(raw, dict):continue
        identity = _producer(raw)
        if identity is None:problems.append('unidentified-'+field)
        else:found.append({'document':str(document), 'field':field, 'identity':identity})
    if directory is not None:
        target = directory/'coordinator-process.json'
        documents[str(target)] = _topology({})
        if target.exists():
            value = read(_plain(target))
            documents[str(target)] = _topology(value)
            identity = _producer(value)
            if identity is None:problems.append('unidentified-coordinator')
            else:found.append({'document':str(target), 'field':None, 'identity':identity})
    return found, problems, documents


def marker(tree, identity, protocol):
    if protocol not in ('desktop', 'source') or not _ID.fullmatch(str(identity)):
        raise ValueError('Invalid supersession identity')
    return _plain(_plain(tree)/'memory/.control/update-supersessions'/(protocol+'-'+identity+'.json'))


def blocked(value, *, protocol=None, current_tree=None):
    """Find exact original-identity proof in its original or caller-bound tree.

    First-start moves data bytes without rewriting old job identities. Its
    validated caller supplies the new tree explicitly; no profile discovery or
    rewritten identity is inferred here.
    """
    if not isinstance(value, dict) or not _ID.fullmatch(str(value.get('id') or '')):return False
    protocol = protocol or ('source' if value.get('directory') or
        (_SOURCE_ID.fullmatch(str(value['id'])) and value.get('kind') not in KINDS) else 'desktop')
    expected = _identity(value, protocol)
    required = ('id', 'program', 'tree', 'kind') if protocol == 'desktop' else ('id', 'program', 'tree')
    for tree in (value.get('tree'), current_tree):
        if tree is None:continue
        try:record = read(marker(tree, value['id'], protocol))
        except (OSError, ValueError, TypeError, KeyError):continue
        bound = record.get('identity') or {}
        if (record.get('format') == FORMAT and all(bound.get(key) == expected.get(key) for key in required)
                and all(bound.get(key) == expected[key] for key in ('directory', 'input_sha256')
                        if protocol == 'source' and expected.get(key) is not None)):
            return True
    return False


def fingerprint(plan):
    material = {key:plan[key] for key in ('format', 'program', 'old_programs', 'trees', 'records')}
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _source_directory(request, old, tree):
    identity = str(request.get('id') or '')
    if not _SOURCE_ID.fullmatch(identity):raise ValueError('Invalid source update identity')
    directory = _plain(request['directory'])
    if directory not in (old/'.source-updates'/identity, tree/'memory/.source-updates'/identity):
        raise ValueError('Source update directory leaves captured programme/data scopes')
    return directory


def _file_proof(path):
    path = _plain(path)
    if not path.exists():return {'$sha256':None}
    if not path.is_file():raise ValueError('Producer evidence is not a regular file')
    return {'$sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def _unit_listing(directory):
    directory = _plain(directory)
    if not directory.exists():return []
    if not directory.is_dir():raise ValueError('Producer manifest is not a directory')
    return sorted(_plain(path).name for path in directory.iterdir())


def _named_producers(operation, directory, tree):
    """Read only tasks/reviews explicitly referenced by this source operation.

    Trusted merger/reviewer tasks have no shell/delegation tools. Every actual
    unit still needs terminal evidence AND exact process absence; a result or a
    liveness heartbeat alone never proves death. Receipt files remain untouched.
    """
    producers, problems, documents = [], [], {}
    task_refs, review_refs = set(), {}
    attempts, rounds = operation.get('attempts') or [], operation.get('agent_review_rounds') or []
    if not isinstance(attempts, list) or not isinstance(rounds, list):
        return [], ['invalid-named-producer-reference'], {}
    for row in [operation, *attempts, *rounds]:
        if not isinstance(row, dict):
            problems.append('invalid-named-producer-reference');continue
        if row.get('task_id'):
            if not row.get('unit_id'):problems.append('named-merger-unit-missing')
            else:task_refs.add((str(row['task_id']), str(row['unit_id'])))
        if row.get('review_id'):
            review_refs.setdefault(str(row['review_id']), set()).add(str(row.get('subject_sha256') or ''))

    def task(task_id, named_units, metadata_key, expected, critics=None):
        if not re.fullmatch(r'code-[0-9a-f]{8}', task_id):raise ValueError('Invalid named producer task')
        root = _plain(tree/'memory/.forge/tasks'/task_id)
        task_path = root/'task.json'
        record = read(task_path);metadata = record.get(metadata_key)
        documents[str(task_path)] = _file_proof(task_path)
        if (record.get('id') != task_id or not isinstance(metadata, dict)
                or metadata.get('schema') != 1
                or any(metadata.get(key) != value for key, value in expected.items())):
            raise ValueError('Named task subject differs from the captured source operation')
        seen = set()
        for kind in ('agents', 'processes', 'verifications'):
            collection = _plain(root/kind)
            names = _unit_listing(collection)
            documents[str(collection)] = {'$listing':names}
            for name in names:
                unit = _plain(collection/name)
                if not unit.is_dir():raise ValueError('Unexpected producer manifest entry')
                request_path, result_path, stop_path = unit/'request.json', unit/'result.json', unit/'stop.json'
                request, result, stop = read(request_path), read(result_path), read(stop_path)
                for target in (request_path, result_path, stop_path):
                    documents[str(target)] = _file_proof(target)
                if request.get('id') != name or request.get('task_id') != task_id:
                    raise ValueError('Named producer unit identity differs')
                # The installed source adapters do not admit commands,
                # verifications or delegated children. Unexpected units are
                # uncertainty, not permission to infer their missing birth.
                if (kind != 'agents' or request.get(metadata_key) != metadata
                        or request.get('parent_unit_id')):
                    raise ValueError('Unexpected producer outside the trusted task adapter')
                role = 'worker' if metadata_key=='source_update' else 'reviewer'
                if request.get('role') != role:raise ValueError('Named producer role differs')
                if result.get('status') not in _UNIT_TERMINAL or stop.get('state') in {'pending', 'unresolved'}:
                    raise ValueError('Named producer terminal custody is unconfirmed')
                if stop.get('result_sha256') and stop['result_sha256'] != documents[str(result_path)]['$sha256']:
                    raise ValueError('Named producer Stop/result binding differs')
                seen.add(name)
                critic = (critics or {}).get(name)
                if critic:
                    if critic.get('result_path') and not _same_path(critic['result_path'], result_path):
                        raise ValueError('Reviewer result leaves its captured unit')
                    if critic.get('result_sha256') and critic['result_sha256'] != documents[str(result_path)]['$sha256']:
                        raise ValueError('Reviewer result digest differs')
                if (request.get('status')=='queued' and not request.get('supervisor_pid')
                        and not request.get('process_identity')
                        and result.get('never_started') is True and stop.get('state')=='settled'):
                    continue  # Existing measured no-admission receipt, not an invented PID.
                identity = _producer(request.get('process_identity'))
                if (identity is None or identity.get('adapter')!='psutil-v1'
                        or request.get('supervisor_pid') != identity['pid']):
                    raise ValueError('Named producer exact PID/birth is unavailable')
                producers.append({'document':str(request_path), 'field':'process_identity', 'identity':identity})
        if not set(named_units).issubset(seen):raise ValueError('Named producer unit receipt is missing')

    grouped = {}
    for task_id, unit_id in task_refs:grouped.setdefault(task_id, set()).add(unit_id)
    for task_id, units in sorted(grouped.items()):
        try:task(task_id, units, 'source_update', {
            'root':str(directory), 'plan_id':operation['id'], 'input_sha256':operation['input_sha256']})
        except (OSError, ValueError, TypeError, KeyError):problems.append('named-merger-custody-unconfirmed')
    for review_id, subjects in sorted(review_refs.items()):
        try:
            if not re.fullmatch(r'review-[0-9a-f]{16}', review_id):raise ValueError('Invalid review identity')
            coordinator = operation.get('coordinator') or {}
            if not isinstance(coordinator, dict):raise ValueError('Invalid review coordinator binding')
            store = '.source-update-reviews' if coordinator.get('review_store')=='isolated-v1' else '.self-review'
            target = _plain(tree/'memory'/store/review_id/'request.json')
            review = read(target);documents[str(target)] = _file_proof(target)
            critics = review.get('critics')
            if (review.get('schema')!='helene.self-review.request.v1' or review.get('id')!=review_id
                    or review.get('execution') not in {'done', 'error', 'cancelled'}
                    or len(subjects)!=1 or not _SOURCE_ID.fullmatch(next(iter(subjects)))
                    or subjects!={review.get('subject_sha256')}
                    or not isinstance(critics, list) or not critics):
                raise ValueError('Review execution evidence is not terminal')
            units = {}
            for critic in critics:
                if not isinstance(critic, dict):raise ValueError('Invalid review critic')
                unit = str(critic.get('unit_id') or '')
                if (not _ID.fullmatch(unit) or unit in units
                        or critic.get('execution') not in {'done', 'error', 'cancelled', 'stopped'}):
                    raise ValueError('Review critic execution identity is unconfirmed')
                units[unit] = critic
            task(str(review['task_id']), units, 'self_review', {
                'root':str(_plain(review['snapshot'])), 'subject_sha256':review['subject_sha256']}, units)
        except (OSError, ValueError, TypeError, KeyError):problems.append('named-review-custody-unconfirmed')
    return producers, problems, documents


def _staging_producers(job, old, trees):
    if not job.get('staging'):return [], [], {}
    from .program_state import staging_belongs
    try:staging = _plain(job['staging'])
    except (OSError, ValueError, TypeError):return [], ['unidentified-staging'], {}
    if not staging_belongs(old, staging):return [], ['staging-leaves-old-programme-scope'], {}
    target = staging/'Helene/code-transition-plan.json'
    record = read(_plain(target))
    if not isinstance(record.get('plans'), list):return [], ['staging-producer-manifest-missing'], {}
    producers, problems = [], []
    documents = {str(target):_topology(record)}
    for row in record['plans']:
        try:
            directory = _plain(row['directory'])
            identity = directory.name
            if not _SOURCE_ID.fullmatch(identity):raise ValueError('Invalid staged source identity')
            allowed = directory == old/'.source-updates'/identity or any(
                directory == tree/'memory/.source-updates'/identity for tree in trees)
            if not allowed:raise ValueError('Staged source producer leaves captured scope')
            operation_path = directory/'pipeline.json'
            operation = read(operation_path)
            if operation.get('id') != identity:raise ValueError('Staged source identity changed')
            tree = next((tree for tree in trees if _same_path(operation.get('tree'), tree)), None)
            if tree is None:raise ValueError('Staged source data leaves captured scope')
            binding = operation.get('program')
            if binding and not _same_path(binding, old):raise ValueError('Staged source belongs to another programme')
            if not binding and not directory.is_relative_to(old):raise ValueError('Staged source lacks programme binding')
            found, errors, captured = _producers(operation, document=operation_path, directory=directory)
            documents.update(captured)
            producers.extend(found);problems.extend(errors)
            if not found:problems.append('staging-source-producer-unidentified')
            named, errors, captured = _named_producers(operation, directory, tree)
            producers.extend(named);problems.extend(errors);documents.update(captured)
        except (OSError, ValueError, TypeError, KeyError):
            problems.append('staging-producer-scope-unconfirmed')
    return producers, problems, documents


def capture(current_program, old_programs, trees):
    """Read current desktop jobs and the flat source-request index only."""
    from . import desktop_update_request as jobs, source_update as source
    current = _plain(current_program)
    old = list(dict.fromkeys(_plain(value) for value in old_programs))
    selected = list(dict.fromkeys(_plain(value) for value in trees))
    if any(current == root or current.is_relative_to(root) or root.is_relative_to(current) for root in old):
        raise ValueError('Current programme overlaps superseded programme scope')
    records = []
    for tree in selected:
        pointer = _plain(jobs.path(tree))
        desktop = read(pointer)
        if (desktop.get('kind') in KINDS and _ID.fullmatch(str(desktop.get('id') or ''))
                and desktop.get('state') not in TERMINAL and _same_path(desktop.get('tree'), tree)):
            owner = next((root for root in old if _same_path(desktop.get('program'), root)), None)
            if owner is not None:
                producers, problems, documents = _producers(desktop, document=pointer)
                staged, staged_problems, staged_documents = _staging_producers(desktop, owner, selected)
                producers.extend(staged);problems.extend(staged_problems)
                documents.update(staged_documents)
                records.append({'protocol':'desktop', 'program':str(owner), 'tree':str(tree),
                    'id':desktop['id'], 'path':str(pointer), 'current':True,
                    'identity':_identity(desktop, 'desktop'), 'producers':producers, 'documents':documents,
                    'problems':problems, 'snapshot':desktop})
        source_pointer = _plain(source.path(tree))
        current_request = read(source_pointer)
        candidates = {}
        index = _plain(source_pointer.with_name('source-update-requests'))
        if index.is_dir():
            for target in index.iterdir():
                if target.suffix == '.json' and _SOURCE_ID.fullmatch(target.stem) and target.is_file():
                    request = read(_plain(target))
                    if request.get('id') == target.stem:candidates[request['id']] = (request, target, False)
        if _SOURCE_ID.fullmatch(str(current_request.get('id') or '')):
            candidates[current_request['id']] = (current_request, source_pointer, True)
        for request, target, is_current in candidates.values():
            if request.get('state') in TERMINAL or not _same_path(request.get('tree'), tree):continue
            explicit = request.get('program')
            if explicit and not any(_same_path(explicit, root) for root in old):continue
            for owner in old:
                try:directory = _source_directory(request, owner, tree)
                except (OSError, ValueError, TypeError, KeyError):continue
                operation_path = directory/'pipeline.json'
                operation = read(_plain(operation_path))
                if operation.get('id') != request['id'] or operation.get('state') in TERMINAL:continue
                if not _same_path(operation.get('tree'), tree):continue
                operation_program = operation.get('program')
                if operation_program and not _same_path(operation_program, owner):continue
                # Shared canonical data alone is never programme ownership.
                if not explicit and not operation_program and not directory.is_relative_to(owner):continue
                if explicit and not _same_path(explicit, owner):continue
                producers, problems, documents = _producers(operation, document=operation_path, directory=directory)
                documents[str(_plain(target))] = _topology(request)
                named, errors, captured = _named_producers(operation, directory, tree)
                producers.extend(named);problems.extend(errors);documents.update(captured)
                records.append({'protocol':'source', 'program':str(owner), 'tree':str(tree),
                    'id':request['id'], 'path':str(_plain(target)), 'current':is_current,
                    'directory':str(directory), 'identity':_identity(request, 'source'),
                    'operation_identity':_identity(operation, 'source'), 'producers':producers, 'documents':documents,
                    'problems':problems, 'snapshot':request, 'operation_snapshot':operation})
                break
    # Parent desktop admission is also a captured old producer for an explicitly
    # linked agent-owned source plan; it never borrows the current new host.
    desktop_by_id = {row['id']:row for row in records if row['protocol']=='desktop'}
    for row in records:
        if row['protocol'] != 'source':continue
        control = (row['operation_snapshot'].get('coordinator') or {}).get('job_control') or {}
        parent = desktop_by_id.get(control.get('id'))
        if (parent and _same_path(control.get('path'), parent['path'])
                and _same_path(control.get('tree'), parent['tree'])):
            row['producers'].extend(copy.deepcopy(parent['producers']))
            row['problems'].extend(parent['problems'])
            for document, captured in parent['documents'].items():
                if document in row['documents'] and row['documents'][document] != captured:
                    row['problems'].append('producer-document-changed-during-capture')
                row['documents'][document] = copy.deepcopy(captured)
    for row in records:
        if not row['producers']:row['problems'].append('no-captured-producer-identity')
    plan = {'format':FORMAT, 'program':str(current), 'old_programs':list(map(str, old)),
            'trees':list(map(str, selected)), 'records':records}
    plan['sha256'] = fingerprint(plan)
    return plan


def _current(row):
    from . import desktop_update_request as jobs, source_update as source
    tree = _plain(row['tree'])
    pointer = jobs.path(tree) if row['protocol']=='desktop' else source.path(tree)
    if row['current'] and read(pointer).get('id') != row['id']:return None, None, 'current-job-replaced'
    request = read(_plain(row['path']))
    if _identity(request, row['protocol']) != row['identity']:return None, None, 'job-identity-changed'
    if request.get('state') in TERMINAL or blocked(request, protocol=row['protocol']):
        return None, None, 'already-terminal'
    operation = None
    if row['protocol']=='source':
        target = _plain(row['directory'])/'pipeline.json'
        operation = read(target)
        if operation:
            if _identity(operation, 'source') != row['operation_identity']:return None, None, 'pipeline-identity-changed'
            if operation.get('state') in TERMINAL:return None, None, 'already-terminal'
        elif target.is_file() or not _plain(row['directory']).is_relative_to(_plain(row['program'])):
            return None, None, 'pipeline-missing-or-unreadable'
        else:
            operation = None
    return request, operation, None


def _producer_records_current(row):
    for raw, captured in row['documents'].items():
        document = _plain(raw)
        if not document.exists() and document.is_relative_to(_plain(row['program'])) and not _plain(row['program']).exists():
            continue  # Its captured programme scope has been removed.
        try:
            if '$sha256' in captured:actual = _file_proof(document)
            elif '$listing' in captured:actual = {'$listing':_unit_listing(document)}
            else:actual = _topology(read(document))
        except (OSError, ValueError, TypeError):return 'producer-evidence-unreadable'
        if actual != captured:return 'producer-topology-changed'
    for producer in row['producers']:
        document = _plain(producer['document'])
        current = read(document)
        if current:
            actual = _producer(current.get(producer['field'])) if producer['field'] else _producer(current)
            if actual != producer['identity']:return 'producer-identity-changed'
        elif document.exists():return 'producer-document-unreadable'
        elif not document.is_relative_to(_plain(row['program'])) or _plain(row['program']).exists():
            return 'producer-document-missing'
    return None


def _observe(row, observe):
    reason = _producer_records_current(row)
    if reason:return None, reason
    observations = []
    for producer in row['producers']:
        try:state = observe(copy.deepcopy(producer['identity']))
        except Exception:return None, 'producer-observation-failed'
        observations.append({'identity':producer['identity'], 'state':state})
        if state not in ('absent', 'changed'):return None, 'producer-'+str(state)
    return observations, None


def retire(plan, *, observe=None):
    """Supersede exact captured metadata after replacement; never rediscover."""
    from . import desktop_update_request as jobs, source_update as source
    if plan.get('format') != FORMAT or plan.get('sha256') != fingerprint(plan):
        raise ValueError('Captured supersession plan changed')
    if observe is None:
        from .session_host import process_state
        observe = process_state
    current = _plain(plan['program'])
    old = {_plain(value) for value in plan['old_programs']}
    trees = {_plain(value) for value in plan['trees']}
    report = {'superseded':[], 'skipped':[], 'blocked':[]}
    for row in plan['records']:
        key = {'id':row['id'], 'protocol':row['protocol'], 'tree':row['tree']}
        if _plain(row['program']) not in old or _plain(row['tree']) not in trees or _plain(row['program']) == current:
            raise ValueError('Captured job leaves supersession scope')
        # Source writers use their shared pipeline lock; desktop writers use
        # the cross-process document transaction. Take both for source records.
        lock = jobs.path(row['tree']) if row['protocol']=='desktop' else source.path(row['tree'])
        with source._LOCK if row['protocol']=='source' else nullcontext():
            with transaction(lock):
                request, operation, reason = _current(row)
                if reason:
                    report['skipped'].append({**key, 'reason':reason})
                    continue
                if row['problems']:
                    report['blocked'].append({**key, 'reason':','.join(row['problems'])})
                    continue
                observations, reason = _observe(row, observe)
                if reason:
                    report['blocked'].append({**key, 'reason':reason})
                    continue
                # Observation callbacks may race a newer current job. Recheck
                # after the final PID observation before any durable write.
                latest, latest_operation, reason = _current(row)
                if reason or latest != request or latest_operation != operation:
                    report['skipped'].append({**key, 'reason':reason or 'job-changed-during-observation'})
                    continue
                reason = _producer_records_current(row)
                if reason:
                    report['blocked'].append({**key, 'reason':reason})
                    continue
                proof = {'format':FORMAT, 'identity':row['identity'], 'replacement_program':str(current),
                    'reason':REASON, 'at':time.time(), 'producer_states':observations}
                write(marker(row['tree'], row['id'], row['protocol']), proof)
                details = {'state':'superseded', 'superseded_reason':REASON,
                           'superseded_by':str(current), 'supersession':proof}
                if row['protocol']=='desktop':
                    # Existing monotonic cancellation blocks stale checkpoint
                    # writers/resume without pretending that tests passed.
                    history = _plain(jobs.path(row['tree']).parent/'desktop-update-jobs'/(row['id']+'.json'))
                    cancel = _plain(history.with_name(row['id']+'.cancel.json'))
                    write(cancel,
                          {'id':row['id'], 'reason':REASON, 'at':proof['at'], 'replacement_program':str(current)})
                    request.update(details, cancel_requested=True)
                    jobs._save(row['tree'], request)
                else:
                    if operation is not None:
                        operation.update(details)
                        write(_plain(row['directory'])/'pipeline.json', operation)
                        write(_plain(row['directory'])/'superseded.json', proof)
                    request.update(details)
                    target = _plain(source.path(row['tree']).with_name('source-update-requests')/(row['id']+'.json'))
                    write(target, request)
                    if row['current']:write(source.path(row['tree']), request)
                report['superseded'].append(key)
    return report
