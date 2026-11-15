import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from deskd import run_control
from unittest.mock import patch


class ExactRunControl(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.run='run-fixture'
        path=self.root/'memory/runs/2026-11'/self.run/'manifest.json'
        path.parent.mkdir(parents=True);path.write_text(json.dumps({'context':{'run_id':self.run},'status':'running'}))

    def test_concurrent_requests_share_identity_and_reject_unknown_or_traversal(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            values=list(pool.map(lambda _:run_control.request(self.root,self.run),range(10)))
        self.assertEqual(len({v['id'] for _,v in values}),1)
        for invalid in ('run-missing','../run-fixture','old'):
            with self.assertRaises(ValueError):run_control.request(self.root,invalid)

    def test_stale_and_pending_receipts_never_confirm_new_reset(self):
        path,ask=run_control.request(self.root,self.run)
        path.with_suffix('.receipt').write_text(json.dumps({'request_id':'old','ok':True}))
        def settle():
            time.sleep(.12)
            path.with_suffix('.receipt').write_text(json.dumps({'request_id':ask['id'],'ok':False}))
            time.sleep(.12)
            path.with_suffix('.receipt').write_text(json.dumps({'request_id':ask['id'],'ok':True}))
        worker=threading.Thread(target=settle);worker.start()
        started=time.monotonic();answer=asyncio.run(run_control.cancel(self.root,self.run));worker.join()
        self.assertTrue(answer['confirmed']);self.assertGreater(time.monotonic()-started,.2)
        self.assertEqual(answer['receipt']['request_id'],ask['id'])
        self.assertEqual(answer['display_note'],{'schema':'helene.run-control.v1','kind':'confirmed'})

    def test_no_owner_proof_remains_accepted_but_unconfirmed(self):
        answer=asyncio.run(run_control.cancel(self.root,self.run))
        self.assertTrue(answer['accepted']);self.assertFalse(answer['confirmed'])
        self.assertEqual(answer['receipt'],{})
        self.assertEqual(answer['display_note'],{'schema':'helene.run-control.v1','kind':'pending'})

    def test_known_refusal_has_recipe_before_request_publication(self):
        for invalid,kind in (('../run-fixture','invalid_id'),('run-missing','ambiguous_id')):
            answer=asyncio.run(run_control.cancel(self.root,invalid))
            self.assertFalse(answer['ok']);self.assertFalse(answer['accepted']);self.assertFalse(answer['confirmed'])
            self.assertEqual(answer['display_note'],{'schema':'helene.run-control.v1','kind':kind})
        self.assertFalse((self.root/'memory/.control/run-cancel').exists())

    def test_unowned_os_diagnostic_remains_literal_without_caption_metadata(self):
        raw='Literal Русский 中文 {owner}'
        with patch.object(run_control,'request',side_effect=OSError(raw)):
            answer=asyncio.run(run_control.cancel(self.root,self.run))
        self.assertEqual(answer['note'],raw);self.assertNotIn('display_note',answer)


if __name__=='__main__':unittest.main()
