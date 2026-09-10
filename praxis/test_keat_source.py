"""Synthetic disk fixtures written by the actual durable RunManager schema."""
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from keat_candidate import ContractError
from keat_source import (SourcePins, RunProvenance, UnsupportedSource,
                         adapt_model_input)
from run_context import RunContext
from run_manager import RunManager
from run_resume import plan_resume


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class TestKeatSource(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        manager = RunManager(self.base)
        ctx = manager.create(RunContext.create(
            run_id='run-synthetic-keat', kind='chat', goal='synthetic fixture',
            principal_id='synthetic:owner', scope='owner',
            origin_chat_id='100', delivery_chat_id='100'), '# Synthetic only\n')
        self.input = {'system': [{'type': 'text', 'text': 'synthetic'}],
                      'tools': [{'name': 'fixture', 'input_schema': {'type': 'object'}}],
                      'messages': [
                          {'role': 'user', 'content': 'identical'},
                          {'role': 'user', 'content': 'identical'},
                          {'role': 'assistant', 'content': [
                              {'type': 'tool_use', 'id': 't1', 'name': 'fixture', 'input': {}}]},
                          {'role': 'user', 'content': [
                              {'type': 'tool_result', 'tool_use_id': 't1', 'content': [
                                  {'type': 'image', 'source': {'type': 'base64',
                                   'media_type': 'image/png', 'data': 'c3ludGhldGlj'}}]}]}]}
        manager.store_result(ctx.run_id, encoded(self.input).decode(),
                             call_id='call-synthetic', name='model-input',
                             media_type='application/json; charset=utf-8',
                             event_kind='model_input', idempotent=True)
        manager.append_event(ctx.run_id, 'model_started', call_id='call-synthetic', role='voice')
        directory = next(self.base.rglob('manifest.json')).parent
        self.args = dict(manifest_bytes=(directory / 'manifest.json').read_bytes(),
                         events_bytes=(directory / 'events.jsonl').read_bytes(),
                         model_input_bytes=(directory / 'results/0001-model-input.log').read_bytes(),
                         expected_provenance=RunProvenance(ctx.run_id, ctx.principal_id,
                             ctx.scope, ctx.origin_chat_id, ctx.delivery_chat_id),
                         call_id='call-synthetic')
        self.repin()

    def repin(self):
        # Test-only evidence authority. Production must NOT self-pin untrusted data.
        self.args['pins'] = SourcePins('synthetic-store', *(
            sha(self.args[k]) for k in ('manifest_bytes', 'events_bytes', 'model_input_bytes')))

    def rows(self):
        return [json.loads(x) for x in self.args['events_bytes'].splitlines()]

    def set_rows(self, rows):
        self.args['events_bytes'] = b''.join(json.dumps(r).encode() + b'\n' for r in rows)
        self.repin()

    def test_durable_roundtrip_is_lossless_but_audience_unsupported(self):
        snapshot = adapt_model_input(**self.args)
        self.assertEqual(snapshot.model_input, self.input)
        self.assertEqual(len(set(snapshot.occurrences)), 4)
        self.assertNotEqual(snapshot.occurrences[0], snapshot.occurrences[1])
        self.assertEqual(snapshot.occurrences, adapt_model_input(**copy.deepcopy(self.args)).occurrences)
        self.assertEqual(snapshot.provenance, self.args['expected_provenance'])
        with self.assertRaises(UnsupportedSource):
            snapshot.require_candidate_authority()

    def interrupted_tool_response_fixture(self, *, completed):
        # All durable writes stay inside TemporaryDirectory. No executor, agent,
        # provider or tool implementation is imported/called: only resume planning.
        manager = RunManager(self.base)
        run_id = self.args['expected_provenance'].run_id
        manager.transition(run_id, 'running', expected='pending')
        directory = manager.path(run_id)
        snapshots = []
        previous_input = None
        for number, call_id in enumerate(('call-synthetic', 'call-resumed')):
            if number:
                manager.resume(run_id, actor='synthetic:owner', reason='synthetic recovery')
                if not completed:
                    manager.store_result(
                        run_id, 'synthetic durable tool result',
                        call_id='fixture-read-0', name='fixture', idempotent=True)
                # Synthetic next call after recovery, retaining the exact prefix.
                # No continuation executor or model is run.
                value = copy.deepcopy(previous_input)
                value['messages'].extend([
                    {'role': 'assistant', 'content': copy.deepcopy(output['blocks'])},
                    {'role': 'user', 'content': [{'type': 'tool_result',
                     'tool_use_id': 'fixture-read-0',
                     'content': 'synthetic durable tool result'}]}])
                manager.store_result(
                    run_id, encoded(value).decode(), call_id=call_id,
                    name='model-input', media_type='application/json; charset=utf-8',
                    event_kind='model_input', idempotent=True)
                manager.append_event(run_id, 'model_started', call_id=call_id,
                                     role='voice')
            else:
                value = copy.deepcopy(self.input)
            tool_id = f'fixture-read-{number}'
            output = {'text': '', 'stop_reason': 'tool_use',
                      'blocks': [{'type': 'tool_use', 'id': tool_id,
                                  'name': 'fixture', 'input': {'synthetic': number}}]}
            manager.store_result(
                run_id, encoded(output).decode(), call_id=call_id,
                name='model-output', media_type='application/json; charset=utf-8',
                event_kind='model_output', idempotent=True)
            manager.append_event(run_id, 'model_completed', call_id=call_id,
                                 role='voice', stop_reason='tool_use')
            manager.start_tool(run_id, tool_id, 'fixture', {'synthetic': number},
                               side_effect=False)
            result_ref = None
            if completed:
                result_ref = manager.store_result(
                    run_id, 'synthetic durable tool result', call_id=tool_id,
                    name='fixture', idempotent=True)
            manager.transition(
                run_id, 'paused', expected='running',
                reason='process restarted; no uncertain side effect observed')

            # Reopen the actual files, not a mocked in-memory resume plan.
            reader = RunManager(self.base)
            # Public reads refresh the persistent lock identity, not evidence.
            before = {p.relative_to(directory): p.read_bytes()
                      for p in directory.rglob('*') if p.is_file() and p.name != '.run.lock'}
            plan = plan_resume(reader, run_id)
            self.assertEqual(plan.kind, 'replay_model_tool_response', plan.reason)
            self.assertTrue(plan.auto_resume)
            self.assertEqual(plan.model_input, value)
            self.assertEqual(plan.model_output, output)
            if previous_input is not None:
                self.assertNotEqual(plan.model_input, previous_input)
            self.assertEqual(len(plan.tool_calls), 1)
            tool = plan.tool_calls[0]
            self.assertEqual(tool.call_id, tool_id)
            self.assertEqual(tool.state, 'completed' if completed else 'outstanding')
            self.assertEqual(tool.replayable, not completed)
            if completed:
                self.assertEqual(tool.result_ref, result_ref)
            else:
                self.assertEqual(tool.replay_basis, 'read_only')

            rows = reader.events(run_id, strict=True)
            input_row, = [r for r in rows
                          if r['kind'] == 'model_input' and r['call_id'] == call_id]
            output_row, = [r for r in rows
                           if r['kind'] == 'model_output' and r['call_id'] == call_id]
            self.assertLess(input_row['seq'], output_row['seq'])
            raw_input = (directory / input_row['result']['path']).read_bytes()
            self.assertEqual(json.loads(raw_input), plan.model_input)
            artifacts = ((directory / 'manifest.json').read_bytes(),
                         (directory / 'events.jsonl').read_bytes(), raw_input)
            snapshot = adapt_model_input(
                manifest_bytes=artifacts[0], events_bytes=artifacts[1],
                model_input_bytes=artifacts[2],
                # This fixture owns its synthetic evidence, not untrusted data.
                pins=SourcePins('synthetic-store', *(sha(b) for b in artifacts)),
                expected_provenance=self.args['expected_provenance'], call_id=call_id)
            self.assertEqual(snapshot.model_input, plan.model_input)
            for index, occurrence in enumerate(snapshot.occurrences):
                self.assertEqual(occurrence.event_id, input_row['id'])
                self.assertEqual(occurrence.call_id, call_id)
                self.assertEqual(occurrence.result_id, input_row['result']['result_id'])
                self.assertEqual(occurrence.index, index)
            with self.assertRaises(UnsupportedSource):
                snapshot.require_candidate_authority()
            self.assertEqual(before, {p.relative_to(directory): p.read_bytes()
                                      for p in directory.rglob('*') if p.is_file() and p.name != '.run.lock'})
            snapshots.append(snapshot)
            previous_input = value

        first, resumed = snapshots
        self.assertEqual(first.model_input['messages'],
                         resumed.model_input['messages'][:len(self.input['messages'])])
        self.assertEqual(first.model_input['tools'], resumed.model_input['tools'])
        self.assertTrue(set(first.occurrences).isdisjoint(resumed.occurrences))

    def test_disk_resume_interrupted_read_only_tool(self):
        self.interrupted_tool_response_fixture(completed=False)

    def test_disk_resume_completed_tool_before_checkpoint(self):
        self.interrupted_tool_response_fixture(completed=True)

    def test_tamper_any_pinned_artifact(self):
        for key in ('manifest_bytes', 'events_bytes', 'model_input_bytes'):
            with self.subTest(key=key), self.assertRaises(ContractError):
                adapt_model_input(**dict(self.args, **{key: self.args[key] + b' '}))

    def test_missing_identity(self):
        for field in ('id', 'call_id', 'result'):
            original = dict(self.args)
            rows = self.rows()
            del rows[1][field]
            self.set_rows(rows)
            with self.assertRaises(ContractError):
                adapt_model_input(**self.args)
            self.args = original
        with self.assertRaises(UnsupportedSource):
            adapt_model_input(**dict(self.args, pins=None))

    def test_mixed_scope_and_current_scope_mismatch(self):
        for field, value in [('scope', 'group'), ('principal_id', 'synthetic:other'),
                             ('delivery_chat_id', '200'), ('origin_chat_id', None)]:
            with self.subTest(field=field), self.assertRaises(ContractError):
                adapt_model_input(**dict(self.args, expected_provenance=replace(
                    self.args['expected_provenance'], **{field: value})))
        rows = self.rows()
        rows[1]['result']['run_id'] = 'run-other'
        self.set_rows(rows)
        with self.assertRaises(ContractError):
            adapt_model_input(**self.args)

    def test_reordered_events_even_with_new_pin(self):
        rows = self.rows()
        rows[1], rows[2] = rows[2], rows[1]
        self.set_rows(rows)
        with self.assertRaises(ContractError):
            adapt_model_input(**self.args)

    def test_duplicate_call_or_result(self):
        for duplicate_call in (True, False):
            rows = self.rows()
            rows[2] = dict(rows[1], seq=3, id='run-synthetic-keat:evt:00000003')
            if not duplicate_call:
                rows[2]['call_id'] = 'different-call'
            self.set_rows(rows)
            with self.assertRaises(ContractError):
                adapt_model_input(**self.args)

    def test_reordered_messages_new_outer_pin_still_ref_mismatch(self):
        self.input['messages'].reverse()
        self.args['model_input_bytes'] = encoded(self.input)
        self.repin()
        with self.assertRaises(ContractError):
            adapt_model_input(**self.args)

    def test_duplicate_json_keys_rejected(self):
        self.args['model_input_bytes'] = b'{"system":"a","system":"b","messages":[],"tools":[]}'
        rows = self.rows()
        rows[1]['result'].update(sha256=sha(self.args['model_input_bytes']),
                                 size=len(self.args['model_input_bytes']))
        self.set_rows(rows)
        with self.assertRaises(ContractError):
            adapt_model_input(**self.args)


if __name__ == '__main__':
    unittest.main()
