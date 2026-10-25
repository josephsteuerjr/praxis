"""Canonical archive membership and bounded quiet-retry reuse; synthetic inputs."""
from pathlib import Path
import json,sys,tempfile,threading,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import transport

class ArchiveRetry(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'room.jsonl'
        with transport._INCOMING_SEEN_GUARD:transport._INCOMING_SEEN.clear()
        self.row={'source_id':'one','outgoing':False,'text':'original'}
    def publish(self):return transport._append_jsonl(self.path,self.row,incoming_source_id='one')
    def test_positive_reuse_restart_and_append_invalidation(self):
        self.assertTrue(self.publish())
        with transport._INCOMING_SEEN_GUARD:transport._INCOMING_SEEN.clear()
        with patch.object(transport.json,'loads',wraps=json.loads) as decode:
            self.assertFalse(self.publish());cold=decode.call_count;self.assertGreater(cold,0)
            for _ in range(5):self.assertFalse(self.publish())
            self.assertEqual(decode.call_count,cold)
            transport._append_jsonl(self.path,{'outgoing':True,'text':'later reply'})
            self.assertFalse(self.publish());self.assertGreater(decode.call_count,cold)
        self.assertEqual(len(self.path.read_text(encoding='utf-8').splitlines()),2)
    def test_replacement_and_truncation_cannot_reuse_old_presence(self):
        self.assertTrue(self.publish())
        other=self.path.with_suffix('.new');other.write_text('{}\n',encoding='utf-8');other.replace(self.path)
        self.assertTrue(self.publish());self.assertFalse(self.publish())
        self.path.write_text('',encoding='utf-8')
        self.assertTrue(self.publish());self.assertFalse(self.publish())
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')),self.row)
    def test_concurrent_first_publish_still_has_one_canonical_row(self):
        results=[]
        threads=[threading.Thread(target=lambda:results.append(self.publish())) for _ in range(4)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(sorted(results),[False,False,False,True])
        self.assertEqual(len(self.path.read_text(encoding='utf-8').splitlines()),1)

if __name__=='__main__':unittest.main()
