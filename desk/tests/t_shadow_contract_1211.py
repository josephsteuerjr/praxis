"""Historical schema inspection, paging and the product's default tool mode."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK))
from deskd import readers
from localharness import boot


class ShadowContract(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.entry = self.root / 'memory/.state/shadow/dm-window'
        self.entry.mkdir(parents=True)
        bound = patch.object(readers, 'tree', return_value=self.root)
        bound.start()
        self.addCleanup(bound.stop)
        self.name = '20261112T120000-12345678.md'
        (self.entry / self.name).write_text('# shadow', encoding='utf-8')

    def test_product_default_does_not_reenable_old_dispatcher(self):
        self.assertEqual(boot.env_knobs({})['PRAXIS_TOOLS_POINTERS'], 'off')

    def snapshot(self, tools):
        raw = json.dumps(tools, ensure_ascii=False).encode()
        name = hashlib.sha256(raw).hexdigest() + '.json'
        folder = self.entry / 'schemas'
        folder.mkdir(exist_ok=True)
        (folder / name).write_bytes(raw)
        (self.entry / self.name).with_suffix('.json').write_text(json.dumps({
            'schemas': name, 'run_id': 'run-20261112T120000123456Z-12345678'}), encoding='utf-8')
        return folder / name

    def test_schemas_are_the_historical_snapshot_not_todays_catalog(self):
        tools = [{'name': 'computer', 'description': '<script>old schema</script>',
                  'input_schema': {'properties': {'old_parameter': {'type': 'string'}}}}]
        self.snapshot(tools)
        got = readers.shadow_capture_details('dm-window', self.name)
        self.assertEqual(got['tools'], tools)
        self.assertEqual(got['tools_status'], 'recorded')
        self.assertEqual(got['run_id'], 'run-20261112T120000123456Z-12345678')

    def test_legacy_is_unknown_and_corruption_is_not_reported_as_no_tools(self):
        self.assertIsNone(readers.shadow_capture_details('dm-window', self.name)['tools'])
        path = self.snapshot([{'name': 'computer'}])
        path.write_text('[]', encoding='utf-8')
        got = readers.shadow_capture_details('dm-window', self.name)
        self.assertEqual(got['tools_status'], 'unavailable')
        self.assertIsNone(got['tools'])

    def test_schema_reference_cannot_escape_snapshot(self):
        (self.entry / self.name).with_suffix('.json').write_text('{"schemas":"../../secret.json"}', encoding='utf-8')
        self.assertEqual(readers.shadow_capture_details('dm-window', self.name)['tools_status'], 'unavailable')

    def test_more_than_thirty_snapshots_are_reachable_without_duplicates(self):
        for i in range(65):
            (self.entry / f'20261112T12{i:04d}-abcdef01.md').write_text('# older', encoding='utf-8')
        first = readers.shadow_captures('dm-window')
        second = readers.shadow_captures('dm-window', before=first[-1])
        third = readers.shadow_captures('dm-window', before=second[-1])
        self.assertEqual([len(first), len(second), len(third)], [30, 30, 6])
        self.assertEqual(len(set(first + second + third)), 66)
        self.assertEqual(readers.shadow_captures('dm-window', before='../../secret'), [])


if __name__ == '__main__':
    unittest.main()
