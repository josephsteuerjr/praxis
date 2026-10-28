"""Pure offline contract regressions (canonical praxis_test runner)."""
import copy
from dataclasses import replace
import unittest

from keat_candidate import Anchor, ContractError, bind_boundary, candidate, schema_digest


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.scope = dict(audience=('owner:1',), source_scope='dm:1/run:abc')
        self.messages = [{'role': 'user', 'content': 'same'} for _ in range(4)]
        self.boundary = bind_boundary(self.messages, historical_count=2, **self.scope)

    def build(self, **kwargs):
        args = dict(boundary=self.boundary, tools=[], **self.scope)
        args.update(kwargs)
        return candidate(self.messages, **args)

    def test_identical_occurrences_partition_exactly(self):
        c = self.build()
        self.assertEqual(len(c.historical_a), 2)
        self.assertEqual(len(c.active_roles), 2)
        for original, borrowed in zip(self.messages, c.historical_a + c.active_roles):
            self.assertIs(original, borrowed)

    def test_changed_tape_requires_new_boundary(self):
        for mutation in ('edit', 'append', 'remove'):
            with self.subTest(mutation=mutation):
                rows = copy.deepcopy(self.messages)
                if mutation == 'edit':
                    rows[-1]['content'] = 'edited tail'
                elif mutation == 'append':
                    rows.append(copy.deepcopy(rows[-1]))
                else:
                    rows.pop(0)
                with self.assertRaises(ContractError):
                    candidate(rows, boundary=self.boundary, tools=[], **self.scope)

    def test_scope_changes_rejected_including_narrowing(self):
        wide = bind_boundary(self.messages, historical_count=2,
                             audience=('owner:1', 'guest:2'), source_scope=self.scope['source_scope'])
        with self.assertRaises(ContractError):
            self.build(boundary=wide)
        with self.assertRaises(ContractError):
            self.build(source_scope='group:1/run:abc')
        with self.assertRaises(ContractError):
            self.build(audience=())

    def test_full_schema_identity(self):
        tools = [{'name': 'shell', 'description': 'old', 'input_schema': {'type': 'object'}}]
        original = schema_digest(tools)
        for field, value in [('description', 'new'), ('input_schema', {'type': 'string'})]:
            altered = copy.deepcopy(tools)
            altered[0][field] = value
            self.assertNotEqual(original, schema_digest(altered))
        self.assertNotEqual(schema_digest(None), schema_digest([]))
        self.assertNotEqual(schema_digest(tools + [{'name': 'read'}]),
                            schema_digest([{'name': 'read'}] + tools))

    def test_active_tool_result_and_image_preserved(self):
        active = [
            {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'call-exact',
                                             'name': 'shell', 'input': {'cmd': 'echo hi'}}]},
            {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': 'call-exact', 'is_error': False,
                 'content': [{'type': 'text', 'text': 'exact result\n'},
                             {'type': 'image', 'source': {'type': 'base64',
                              'media_type': 'image/png', 'data': 'AA=='}}]}]}]
        self.messages[2:] = active
        before = copy.deepcopy(self.messages)
        self.boundary = bind_boundary(self.messages, historical_count=2, **self.scope)
        c = self.build()
        self.assertEqual(self.messages, before)
        self.assertIs(c.active_roles[0], active[0])
        self.assertIs(c.active_roles[1]['content'][0]['content'][1],
                      active[1]['content'][0]['content'][1])

    def test_malformed_boundaries(self):
        bad = [None, replace(self.boundary, version=0), replace(self.boundary, count=True),
               replace(self.boundary, historical_end=None),
               replace(self.boundary, active_start=None),
               replace(self.boundary, historical_end=self.boundary.active_start),
               replace(self.boundary, historical_end=Anchor(0, self.boundary.historical_end.message_digest)),
               replace(self.boundary, active_start=Anchor(-1, 'bad')),
               replace(self.boundary, active_start=Anchor(True, 'bad')),
               replace(self.boundary, active_start=Anchor(2, 'bad'))]
        for boundary in bad:
            with self.subTest(boundary=boundary), self.assertRaises(ContractError):
                self.build(boundary=boundary)

    def test_edge_cuts_and_invalid_inputs(self):
        for rows in ([], self.messages):
            for cut in (0, len(rows)):
                boundary = bind_boundary(rows, historical_count=cut, **self.scope)
                c = candidate(rows, boundary=boundary, tools=None, **self.scope)
                self.assertEqual(c.historical_a + c.active_roles, tuple(rows))
        for cut in (-1, 5, True, '2'):
            with self.assertRaises(ContractError):
                bind_boundary(self.messages, historical_count=cut, **self.scope)
        for tools in ([{'name': 'x', 'schema': object()}], [{'n': float('nan')}], [{1: 'x'}]):
            with self.assertRaises(ContractError):
                schema_digest(tools)


if __name__ == '__main__':
    unittest.main()
