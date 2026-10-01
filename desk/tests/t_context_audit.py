"""Context-boundary regressions. No model calls or installed-state writes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'helene/core'), str(ROOT / 'praxis')]
import frame_layout
import memory_life

spec = importlib.util.spec_from_file_location('context_audit', ROOT / 'desk/installer/audit_dialogue_context.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class Boundaries(unittest.TestCase):
    def test_upstream_window_does_not_claim_the_whole_archive(self):
        source = [{'line': 'Earlier owner constraint'}, {'line': 'Later alarm'}]
        selected = memory_life.tape_window(source, max_chars=12)
        self.assertEqual(selected, source[-1:])
        line = ' '.join(map(str, frame_layout._dialogue({
            'tape_delivered': len(selected), 'tape_available': len(selected), 'tape_limit': 100})))
        self.assertNotIn('весь сохранённый разговор', line)
        self.assertIn('полнота архива не установлена', line)
        self.assertNotIn('ОБРЕЗАНО', line)  # No invented count of upstream cuts.

    def test_builder_cut_remains_explicit(self):
        line = ' '.join(map(str, frame_layout._dialogue({
            'tape_delivered': 100, 'tape_available': 140, 'tape_limit': 100})))
        self.assertIn('из 140 — ОБРЕЗАНО сверху', line)

    def test_no_history_does_not_claim_a_conversation(self):
        self.assertEqual(frame_layout._dialogue({'tape_delivered': 0, 'tape_available': 0}), [])

    def fixture(self, folder, requests):
        (folder / 'manifest.json').write_text(json.dumps({'status': 'done'}), encoding='utf8')
        rows = []
        for n, messages in enumerate(requests):
            data = json.dumps({'system': 'private system', 'messages': messages}).encode()
            (folder / f'{n}.json').write_bytes(data)
            rows.append({'kind': 'model_input', 'result': {
                'result_id': str(n), 'path': f'{n}.json', 'sha256': hashlib.sha256(data).hexdigest()}})
        (folder / 'events.jsonl').write_text('\n'.join(map(json.dumps, rows)), encoding='utf8')

    def test_detects_lost_history_without_emitting_private_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            owner = {'role': 'user', 'content': 'private owner constraint'}
            tool = {'role': 'assistant', 'content': 'private tool result'}
            self.fixture(folder, [[owner], [owner, tool], [tool]])
            result = audit.audit_run(folder)
            self.assertEqual([r['previous_prefix_preserved'] for r in result['inputs']], [True, True, False])
            self.assertNotIn('private', json.dumps(result))

    def test_hash_mismatch_is_not_accepted_as_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.fixture(folder, [[{'role': 'user', 'content': 'source'}]])
            (folder / '0.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                audit.audit_run(folder)


if __name__ == '__main__':
    unittest.main()
