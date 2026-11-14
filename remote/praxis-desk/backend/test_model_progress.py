"""Relay progress is a numeric projection bound to its model call."""
import json, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from deskd import readers

class Progress(unittest.TestCase):
    def detail(self,events):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'HELENE_TREE':d}):
            run='run-20261114T000000000000Z-abcd1234';p=Path(d)/'memory/runs/2026-11'/run;p.mkdir(parents=True)
            (p/'manifest.json').write_text('{"status":"running"}')
            (p/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
            return readers.run_detail(run)['iterations']

    def test_exact_call_numeric_projection_and_tail_without_start(self):
        packet={'kind':'model_progress','call_id':'a','at':'now','metrics':{'schema':'relay.generation.v1',
                'argument_bytes':123,'attempt_ms':5000,'argument_deltas':True,'outcome':'streaming','secret':'PRIVATE'}}
        rows=self.detail([{'kind':'model_started','call_id':'a'},{'kind':'model_started','call_id':'b'},packet])
        self.assertEqual(rows[0]['progress'],{'argument_bytes':123,'attempt_ms':5000,'outcome':'streaming'})
        self.assertNotIn('progress',rows[1]);self.assertNotIn('PRIVATE',json.dumps(rows))
        tail=self.detail([packet,{'kind':'model_completed','call_id':'a','duration_ms':9}])
        self.assertEqual(len(tail),1);self.assertEqual(tail[0]['status'],'completed')

    def test_unknown_schema_and_missing_identity_are_ignored(self):
        rows=self.detail([{'kind':'model_started','call_id':'a'},
            {'kind':'model_progress','metrics':{'schema':'relay.generation.v1','argument_bytes':123}},
            {'kind':'model_progress','call_id':'a','metrics':{'schema':'other','argument_bytes':123}}])
        self.assertEqual(len(rows),1);self.assertNotIn('progress',rows[0])

if __name__=='__main__':unittest.main()
