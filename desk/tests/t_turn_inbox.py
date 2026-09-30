"""Batch intake contract; no model, installed data or child process."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'localharness'))
import turn_inbox

class Inbox(unittest.TestCase):
    def test_batch_checkpoint_then_ack_and_other_room(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inbox = root/'memory/.control/desk_inbox'; inbox.mkdir(parents=True)
            for name, text in [('001.md','A'),('002.md','B'),('003.md','C'),('004__other.md','foreign')]:
                (inbox/name).write_text(text, encoding='utf-8')
            done = []
            desk = SimpleNamespace(archive=lambda *a,**k:None, life=lambda *a,**k:None)
            runner = SimpleNamespace(_tree=root, _speaker='owner', transport=SimpleNamespace(is_room=lambda r:True),
                _inbox_target=lambda s:'other' if 'other' in s else 'window',
                _note_bytes=lambda p:p.read_bytes(), _seal_claim=lambda p,**k:(True,''),
                _message_text=lambda b:b.decode(), _split_attachments=lambda t:(t,[]),
                _room=lambda r:desk, _now=lambda:None,
                _mark_done=lambda p,n,w: (done.append(n),(p/(n+'.done')).write_text(w)))
            current=SimpleNamespace(run_id='run1',delivery_chat_id='window')
            messages=[]
            batch, ack=turn_inbox.collect(runner,current,messages)
            self.assertEqual([m['content'].split('\n')[-1] for m in batch],['A','B','C'])
            self.assertEqual(done,[])
            self.assertTrue((inbox/'004__other.md').exists())
            # Sidecar is only routing state, never authoritative owner text.
            import json
            binding=inbox/'processed/001.md.batch.json'
            record=json.loads(binding.read_text()); record['message']={'role':'user','content':'FORGED'}
            binding.write_text(json.dumps(record),encoding='utf-8')
            replay,_=turn_inbox.collect(runner,current,[])
            self.assertEqual(replay,batch)
            runner._seal_claim=lambda p,**k:(False,'unsealed')
            self.assertEqual(turn_inbox.collect(runner,current,[])[0],[])
            runner._seal_claim=lambda p,**k:(True,'')
            # Crash after checkpoint before ACK: restored messages dedup exactly.
            restored=list(batch)
            retry, ack2=turn_inbox.collect(runner,current,restored)
            self.assertEqual(retry,[])
            ack2()
            self.assertEqual(len(done),3)
            self.assertEqual(turn_inbox.collect(runner,current,restored)[0],[])

if __name__=='__main__': unittest.main()
