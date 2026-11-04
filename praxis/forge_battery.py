"""Frozen verification exports and exact canonical-matrix evidence binding.

This module never executes candidate code. The coordinator runs the exported
matrix in an isolated server stand using existing hands, then binds its real
supervisor result. Critic opinion and tested-candidate qualification are distinct.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import shutil

import forge
import forge_intelligence
import forge_review as review
import forge_runtime as runtime


def prepare(task_id,review_id):
    with forge._mutation_lock(task_id):
        reason=forge._admission_reason(task_id)
        if reason:raise ValueError(reason)
        return _prepare(task_id,review_id)


def _prepare(task_id,review_id):
    directory,record=review._record(task_id,review_id)
    if record.get('verification'):return status(task_id,review_id)
    if record['kind']=='merge':
        review.validate_merge(directory,record['input_sha256'],current=True)
        if not record['finished']:raise ValueError('finish exact M before planning verification')
        source=directory/'M'
    else:
        subject=review.validate_snapshot(directory,record['subject_sha256'])
        if set(subject['manifests'])!={'candidate'}:raise ValueError('plan verification on the originating merge record')
        source=directory/'candidate'
    manifest=review.inventory(source)
    source_sha=review.digest(review.encoded(manifest))
    export=directory/'verification/work'
    if export.exists():raise ValueError('partial verification export exists; reconcile instead of overwriting')
    export.parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(source,export)
    if review.inventory(export)!=manifest:raise ValueError('verification export identity mismatch')
    opened=forge.start('Verify exact frozen source composition '+source_sha,target=str(export),isolation='direct',max_active_workers=1)
    match=re.search(r'coding-задача (code-[0-9a-f]{8})',opened)
    if not match:raise ValueError('verification task not created: '+opened)
    tid=match.group(1)
    task=forge.get(tid);task.update(coordinator_task_id=task_id,coordinator_review_id=review_id)
    forge._save_task(task)
    plan=forge_intelligence.verification_plan(export,full=True)
    expected=[row['command'] for row in plan.get('checks',[]) if row.get('command')]
    if not expected:raise ValueError('no authoritative verification checks found')
    record['verification']={'schema':1,'task_id':tid,'root':str(export),'candidate_sha256':source_sha,
        'manifest':manifest,'expected_commands':expected,'expected_checks':plan['checks'],'plan':plan,'state':'planned',
        'execution_contract':'isolated server stand; no production memory/env/network; coordinator chooses host executor'}
    runtime.atomic_json(directory/'record.json',record)
    return status(task_id,review_id)


def status(task_id,review_id):
    directory,record=review._record(task_id,review_id)
    verification=record.get('verification')
    if not verification:return {'state':'not_run','qualified':False}
    report=runtime._read(directory/'verification/report.json',{})
    current=(review.digest(review.encoded(review.inventory(directory/'M')))
             if record['kind']=='merge' else review.digest(review.encoded(review.inventory(directory/'candidate'))))
    evidence_ok=bool(report)
    try:
        if report:
            unit=forge._unit_dir(report['verification_task_id'],'verifications',report['verification_id'])
            evidence=directory/'verification/evidence'
            evidence_ok=((directory/'verification/report.json').read_bytes()==(evidence/'report.json').read_bytes()
                and review.digest((unit/'request.json').read_bytes())==report['request_sha256']
                and review.digest((unit/'result.json').read_bytes())==report['result_sha256'])
            for row in report['logs']:
                file=review.inside(evidence,row['file'])
                raw=file.read_bytes()
                evidence_ok=evidence_ok and len(raw)==row['bytes'] and review.digest(raw)==row['sha256']
    except (OSError,ValueError,KeyError,TypeError):evidence_ok=False
    eligible=(evidence_ok and report.get('candidate_sha256')==current==verification['candidate_sha256']
              and review.inventory(verification['root'])==verification['manifest']
              and (record['kind']!='merge' or not review.stale_merge_inputs(record)))
    return {**verification,'report':report,'evidence_valid':evidence_ok,'qualified':eligible,'state':'tested' if eligible else 'stale' if report else 'planned'}


def bind(task_id,review_id,verification_id,expected_candidate_sha256):
    with forge._mutation_lock(task_id):
        reason=forge._admission_reason(task_id)
        if reason:raise ValueError(reason)
        return _bind(task_id,review_id,verification_id,expected_candidate_sha256)


def _bind(task_id,review_id,verification_id,expected_candidate_sha256):
    directory,record=review._record(task_id,review_id)
    expected=record.get('verification')
    if not expected or expected['candidate_sha256']!=expected_candidate_sha256:
        raise ValueError('exact planned candidate SHA required')
    export=Path(expected['root'])
    if review.inventory(export)!=expected['manifest']:raise ValueError('tested source changed after export')
    if record['kind']=='merge':review.validate_merge(directory,record['input_sha256'],current=True)
    unit=forge._unit_dir(expected['task_id'],'verifications',verification_id)
    request=runtime._read(unit/'request.json');result=runtime._read(unit/'result.json')
    if not request or request.get('task_id')!=expected['task_id'] or Path(request.get('root','')).resolve()!=export.resolve():
        raise ValueError('verification belongs to another composition/task')
    commands=[row['command'] for row in request.get('checks',[]) if row.get('command')]
    if sorted(commands)!=sorted(expected['expected_commands']):raise ValueError('verification is not the complete planned battery')
    if request.get('scope')!='self':raise ValueError('verification must execute the exported local composition')
    planned=expected.get('expected_checks',expected['plan']['checks'])
    received=request.get('checks',[])
    if len(received)!=len(planned):raise ValueError('verification check contract count changed')
    for wanted,actual in zip(planned,received):
        if any(actual.get(key)!=value for key,value in wanted.items()) or actual.get('skip'):
            raise ValueError('verification check execution contract changed')
    if not result or result.get('status')!='passed' or result.get('failed') or result.get('skipped'):
        raise ValueError('verification battery is incomplete, failed, timed out or unknown')
    checks=result.get('checks',[])
    if len(checks)!=len(commands) or any(row.get('status')!='passed' or row.get('exit')!=0 or row.get('dedup') for row in checks):
        raise ValueError('battery lacks actual executed passing checks')
    logs=[];executed=0
    evidence=directory/'verification/evidence';evidence.mkdir(exist_ok=True)
    for index,row in enumerate(checks):
        if row.get('command')!=commands[index] or row.get('id')!=received[index].get('id'):raise ValueError('check result identity mismatch')
        log=Path(row.get('log',''))
        if not log.is_absolute():log=unit/log
        if not log.resolve().is_relative_to((unit/'logs').resolve()) or log.is_symlink() or not log.is_file():
            raise ValueError('verification log escaped producer directory')
        raw=log.read_bytes();text=raw.decode('utf-8',errors='replace')
        counts=[int(number) for pair in re.findall(r'Ran (\d+) tests?|(?:^|\s)(\d+) passed',text) for number in pair if number]
        count=max(counts,default=0);executed+=count
        sha=review.digest(raw);name=f'check-{index:03d}-{sha}.txt'
        saved=evidence/name
        if saved.exists():
            if saved.read_bytes()!=raw:raise ValueError('immutable verification log changed')
        else:
            with saved.open('xb') as stream:stream.write(raw)
        logs.append({'check_id':row.get('id'),'command':commands[index],'sha256':sha,'bytes':len(raw),'tests_observed':count,'file':name})
    if executed<=0:raise ValueError('no actual collected/executed test evidence')
    if review.inventory(export)!=expected['manifest']:raise ValueError('candidate moved during verification binding')
    report={'schema':1,'candidate_sha256':expected_candidate_sha256,'verification_task_id':expected['task_id'],
        'verification_id':verification_id,'request_sha256':review.digest((unit/'request.json').read_bytes()),
        'result_sha256':review.digest((unit/'result.json').read_bytes()),'logs':logs,
        'tests_observed':executed,'status':'passed','at':forge._now(),
        'executor':{'scope':request.get('scope'),'supervisor_pid':result.get('supervisor_pid'),
                    'supervisor_started_at':result.get('supervisor_started_at'),
                    'isolation':'coordinator-managed; not inferred from a passing command'}}
    path=directory/'verification/report.json'
    if path.exists():
        previous=runtime._read(path)
        if any(previous.get(k)!=report.get(k) for k in report if k!='at'):raise ValueError('different verification already bound; retain original')
        report=previous
    else:runtime.atomic_json(path,report)
    runtime.atomic_json(evidence/'report.json',report)
    return status(task_id,review_id)
