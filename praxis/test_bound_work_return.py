"""Follow a named work return through the real scheduler and durable run stores."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import agent
import authored_notes
import forge
import mtproto_runner as runner
import run_context
import run_manager
import tasks
import work_focus
import work_store
from core import events, notices, subagents


@pytest.fixture
def bench(tmp_path, monkeypatch):
    manager = run_manager.RunManager(tmp_path)
    for target, attr, value in (
        (tasks, 'TASKS', tmp_path / 'memory' / 'tasks.json'),
        (forge, 'TASKS_DIR', tmp_path / 'forge'),
        (events, 'JOURNAL', tmp_path / 'events.jsonl'),
        (events, 'DELIVERED', tmp_path / 'delivered.json'),
        (forge, '_wake_load_seen', lambda: set()),
        (forge, 'mark_seen', lambda keys: None),
        (work_store, 'BASE', tmp_path),
        (agent, 'BASE', tmp_path), (agent, '_runs', lambda: manager),
        (runner, '_ONE_MIND', asyncio.Lock()),
        # Own the client for this fixture; another test may have imported the
        # runner with a minimal client that only supports decorator setup.
        (runner, 'client', SimpleNamespace(is_connected=lambda: True)),
        (agent.llm, 'configured', lambda: True),
        (agent.turns, 'record', lambda turn: None),
        (agent, '_self_intent_eligible', lambda goal: False),
        (agent, '_mailbox_evidence', lambda: ''),
        (agent, '_mailbox_frame_block', lambda: ''),
        (notices, 'bind_alarm_run', lambda *args: 0),
    ):
        monkeypatch.setattr(target, attr, value)
    monkeypatch.setenv('PRAXIS_WORK_LOOP', 'on')
    return manager


@pytest.mark.parametrize('order_prefix', [False, True])
def test_bound_live_wake_first_model_input_and_stale_scheduler_row(bench, tmp_path, monkeypatch, order_prefix):
    selected = work_store.open_card(goal='Identical title', origin='praxis:self')
    other = work_store.open_card(goal='Identical title', origin='praxis:self')
    ledger = authored_notes.AuthoredNoteLedger(tmp_path)
    a = ledger.write('Selected next step: verify the packaging receipt.')
    b = ledger.write('Unrelated note must not be carried by title collision.')
    work_focus.bind_note(tmp_path, selected['id'], a['id'])
    work_focus.bind_note(tmp_path, other['id'], b['id'])
    goal = (agent.CODING_PREFIX + ' ' if order_prefix else '') + 'Return to the exact work'
    event = tasks.add('wake', goal, work_id=selected['id'])
    seen = []

    def dispatch(role, *, system, messages, **kwargs):
        run = run_context.current_run(required=True)
        frame = json.dumps(messages, ensure_ascii=False)
        seen.append((run, frame))
        assert run.work_id == selected['id'] and run.kind == 'task_window'
        assert run.principal_id == 'praxis:self' and run.scope == 'owner'
        assert run.origin_chat_id is None and run.delivery_chat_id is None
        assert 'связь открыта' in frame and 'Telethon закрыт на окно' not in frame
        assert 'Заказ Егора: Return to the exact work' not in frame
        assert a['text'] in frame and b['text'] not in frame
        assert tasks.list_open() == []
        return agent.llm.LLMResponse(stop_reason='tool_use', blocks=[{
            'type': 'tool_use', 'id': 'close', 'name': 'task_control',
            'input': {'action': 'done', 'evidence': 'Packaging receipt was verified.'},
        }])

    with monkeypatch.context() as patch:
        patch.setattr(agent, '_pre_model_timing_enabled', lambda: False, raising=False)
        patch.setattr(agent.llm, 'chat', dispatch)
        assert asyncio.run(runner._fire_task(event)) is True
        assert asyncio.run(runner._fire_task(event)) is False
    assert len(seen) == 1
    run, _ = seen[0]
    forensic = run_context.RunContext.from_dict(bench.manifest(run.run_id)['context'])
    assert forensic.work_id == selected['id']
    assert work_store.card(selected['id'])['status'] == 'done'
    assert work_store.card(other['id'])['status'] == 'running'


@pytest.mark.parametrize('edge', ['absent', 'bound', 'unknown'])
def test_crash_between_claim_and_ack_does_not_redial_known_or_partial_work(bench, edge):
    row = work_store.open_card(goal='Return after restart', origin='praxis:self')
    event = tasks.add('window', 'Resume exact work', work_id=row['id'])
    run = run_context.RunContext.create(kind='task_window', goal='Resume exact work',
                                        principal_id='praxis:self', scope='owner', work_id=row['id'],
                                        work_alarm_id=event['id'])
    assert tasks.claim_open(event['id'], 'window', run_id=run.run_id, work_id=row['id'])
    if edge == 'bound':
        bench.create(run, 'owned work snapshot')
        work_store.attach_run(row['id'], run.run_id)
        bench.transition(run.run_id, 'running', expected='pending')
        bench.transition(run.run_id, 'cancelled', expected='running')
    elif edge == 'unknown':
        (bench.root / bench._month(run.created_at) / run.run_id).mkdir(parents=True)
    released = runner._release_stale_task_claims()
    if edge == 'absent':
        assert released == [event['id']] and len(tasks.due()) == 1
    else:
        assert released == [] and tasks.due() == []
        assert bool(tasks.open_claims()) is (edge == 'unknown')


def _forge_child(row, prior, *, task_id='code-f00d1234', child_id='agent-011eabcd'):
    task_dir = forge._task_dir(task_id)
    unit = forge._unit_dir(task_id, 'agents', child_id)
    unit.mkdir(parents=True)
    forge._atomic_json(task_dir / 'task.json', {'id': task_id, 'run_context': {
        'scope': 'owner', 'run_id': prior, 'work_id': row['id'], 'forge_task_id': task_id,
    }})
    forge._atomic_json(unit / 'request.json', {'id': child_id})
    return task_id, child_id, unit


def test_selected_forge_completion_returns_only_through_its_alarm(bench, tmp_path, monkeypatch):
    row = work_store.open_card(goal='My selected work', origin='praxis:self', run_id='prior-run')
    task_id, child_id, unit = _forge_child(row, 'prior-run')
    alarm = tasks.add('wake', 'Return after the chosen result', work_id=row['id'],
                      after_forge=(task_id, child_id))
    monkeypatch.setattr(agent, 'run_is_terminal', lambda run_id: run_id == 'prior-run')
    seen = []
    generic = []
    monkeypatch.setattr(agent, 'forge_event_turn', lambda batch: generic.extend(batch))
    key = subagents.event_key(task_id, child_id)
    foreign = subagents.event_key(task_id, 'agent-beefabcd')

    def dispatch(role, *, system, messages, **kwargs):
        run = run_context.current_run(required=True)
        frame = json.dumps(messages, ensure_ascii=False)
        receipt = forge.observe_focus_child(row, task_id, child_id)
        assert run.work_id == row['id'] and receipt['result_sha256'] in frame
        assert 'Selected Forge result' in frame and child_id in frame
        seen.append(run.run_id)
        return agent.llm.LLMResponse(stop_reason='tool_use', blocks=[{
            'type': 'tool_use', 'id': 'close', 'name': 'task_control',
            'input': {'action': 'done', 'evidence': 'Named receipt checked.'},
        }])

    monkeypatch.setattr(agent.llm, 'chat', dispatch)
    monkeypatch.setattr(agent, '_pre_model_timing_enabled', lambda: False, raising=False)
    assert tasks.due() == []
    events.emit('subagent_result', 'forge', {'task_id': task_id, 'agent_id': child_id}, dedup_key=key)
    events.emit('subagent_result', 'forge', {'task_id': task_id, 'agent_id': 'agent-beefabcd'}, dedup_key=foreign)
    asyncio.run(runner._run_forge_event_pass())
    assert [e['dedup_key'] for e in generic] == [foreign]
    asyncio.run(runner._fire_due_tasks())
    assert not seen and tasks.list_open()[0]['id'] == alarm['id']
    forge._atomic_json(unit / 'result.json', {'status': 'done', 'summary': 'needle'})
    forge._atomic_json(unit / 'stop.json', {'state': 'unresolved'})
    asyncio.run(runner._fire_due_tasks())
    assert not seen
    (unit / 'stop.json').unlink()
    asyncio.run(runner._fire_due_tasks())
    asyncio.run(runner._run_forge_event_pass())
    asyncio.run(runner._fire_due_tasks())
    assert len(seen) == 1 and [e['dedup_key'] for e in generic] == [foreign]
    assert events.undelivered() == [] and tasks.list_open() == []


def test_bound_return_does_not_start_over_a_paused_run_or_cancelled_alarm(bench, monkeypatch):
    row = work_store.open_card(goal='Waiting at a checkpoint', run_id='paused-run')
    alarm = tasks.add('wake', 'Return when I can', work_id=row['id'])
    calls = []
    monkeypatch.setattr(agent, 'run_is_terminal', lambda run_id: False)
    monkeypatch.setattr(agent, 'task_window', lambda *args, **kwargs: calls.append(args))
    assert asyncio.run(runner._fire_task(alarm)) is False
    assert tasks.list_open()[0]['id'] == alarm['id'] and not calls
    assert tasks.cancel(alarm['id'])
    monkeypatch.setattr(agent, 'run_is_terminal', lambda run_id: True)
    assert asyncio.run(runner._fire_task(alarm)) is False and not calls


def test_pausing_during_model_does_not_become_failed(bench, monkeypatch):
    row = work_store.open_card(goal='Hold the current work')
    alarm = tasks.add('wake', 'Return to the exact work', work_id=row['id'])
    seen = []

    def pause_inside_model(*args, **kwargs):
        run = run_context.current_run(required=True)
        seen.append(run.run_id)
        bench.request_pause(run.run_id, actor='desk:owner', reason='Keep this checkpoint')
        agent._run_status_gate(phase='test pause boundary')

    monkeypatch.setattr(agent.llm, 'chat', pause_inside_model)
    monkeypatch.setattr(agent, '_pre_model_timing_enabled', lambda: False, raising=False)
    asyncio.run(runner._fire_task(alarm))
    assert len(seen) == 1
    manifest = bench.manifest(seen[0])
    assert manifest['status'] == 'paused' and manifest['control']['action'] == 'pause'
    assert manifest['context']['work_alarm_id'] == alarm['id']


def test_bound_alarm_rollback_status_preserves_ordinary_pending(bench):
    row = work_store.open_card(goal='Keep this saved work across versions')
    focus = tasks.add('wake', 'Return to the exact work', work_id=row['id'])
    generic = tasks.add('wake', 'Regular alarm')
    saved = json.loads(tasks.TASKS.read_text('utf-8'))
    assert [entry['id'] for entry in saved if entry.get('status') == 'pending'] == [generic['id']]
    assert {entry['id'] for entry in tasks.due()} == {generic['id'], focus['id']}
    assert tasks.cancel(focus['id'])
    assert {entry['id'] for entry in tasks.due()} == {generic['id']}
