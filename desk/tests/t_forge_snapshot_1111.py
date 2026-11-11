"""HTTP-only view caching: bounded staleness, isolation and control invalidation."""
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys,tempfile,threading,time,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deskd import forge_snapshot as view
from deskd import forge_reader


def page(status='stop_pending'):
    return {'active':[{'id':'task','goal':'fixture','observed_at':'original-observation',
             'agents':[{'id':'worker','status':status,'liveness':{'state':'exited'},
                        'payload_refs':{'private':'fixture'},'request_ref':{'private':'fixture'}}]}],
            'recent':[],'next_cursor':None}


class Snapshots(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);view.invalidate();self.addCleanup(view.invalidate)

    def test_idle_unknown_worker_remains_visible_without_repeated_tree_scan(self):
        value=page()
        with patch.object(forge_reader,'activity',return_value=value) as read,patch.object(view.time,'monotonic',return_value=100):
            first=view.activity(self.root);first['active'].clear()
            second=view.activity(self.root)
            self.assertEqual(read.call_count,1)
            self.assertEqual(second,page())
            self.assertEqual(second['active'][0]['observed_at'],'original-observation')
            self.assertEqual(view.interval(second),10)

    def test_active_worker_refreshes_after_one_second_and_idle_after_ten(self):
        for state,ttl in [('running',1),('queued',1),('stop_pending',10)]:
            with self.subTest(state=state):
                view.invalidate()
                with patch.object(forge_reader,'activity',side_effect=lambda *a:page(state)) as read,patch.object(view.time,'monotonic',return_value=100) as clock:
                    view.activity(self.root)
                    clock.return_value=100+ttl-.01;view.activity(self.root)
                    self.assertEqual(read.call_count,1)
                    clock.return_value=100+ttl;view.activity(self.root)
                    self.assertEqual(read.call_count,2)

    def test_scope_pagination_and_phone_redaction_do_not_share_wrong_views(self):
        with patch.object(forge_reader,'activity',side_effect=lambda *a:copy.deepcopy(page())) as read:
            view.activity(self.root,20,'');view.activity(self.root,21,'')
            view.activity(self.root,20,'cursor');view.activity(self.root/'other',20,'')
            limited=view.phone_activity(self.root,20,'')
            self.assertEqual(read.call_count,4)
            unit=limited['active'][0]['agents'][0]
            self.assertEqual(unit.get('payload_refs',{}),{});self.assertNotIn('request_ref',unit)
            self.assertIn('payload_refs',view.activity(self.root)['active'][0]['agents'][0])

    def test_errors_retry_and_invalidation_during_build_does_not_reinstall_old_cache(self):
        with patch.object(forge_reader,'activity',side_effect=forge_reader.ReadError('unreadable')):
            with self.assertRaises(forge_reader.ReadError):view.activity(self.root)
        def during_read(*args):view.invalidate();return page()
        with patch.object(forge_reader,'activity',side_effect=during_read) as read:
            view.activity(self.root);view.activity(self.root)
            self.assertEqual(read.call_count,2)
        with patch.object(forge_reader,'activity',return_value=page()) as read:
            view.activity(self.root);view.invalidate();view.activity(self.root)
            self.assertEqual(read.call_count,2)

    def test_concurrent_views_coalesce_and_storage_is_bounded(self):
        entered=threading.Event();release=threading.Event();calls=[]
        def read(*args):
            calls.append(1);entered.set();self.assertTrue(release.wait(5));return page()
        with patch.object(forge_reader,'activity',side_effect=read),ThreadPoolExecutor(max_workers=3) as pool:
            futures=[pool.submit(view.activity,self.root) for _ in range(3)]
            self.assertTrue(entered.wait(5));release.set()
            self.assertTrue(all(f.result(timeout=5)==page() for f in futures))
        self.assertEqual(len(calls),1)
        with patch.object(forge_reader,'activity',return_value=page()):
            for index in range(view._LIMIT+10):view.activity(self.root,20,str(index))
        self.assertLessEqual(len(view._entries),view._LIMIT)


if __name__=='__main__':unittest.main()
