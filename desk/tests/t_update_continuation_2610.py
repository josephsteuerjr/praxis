"""Durable session continuation replaces the former UI-owned waiting invocation.

The old private _wait_for_review/close-revokes contract was removed in2.3: it had
no production callers. Exact Git/composition/review freshness has causal checks
in t_update_contract_230, and real process close/rejoin in t_session_host_230.
These tests exercise the current durable admission/pause/prepared-resume seams.
"""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import python_updates as updates,desktop_update_request as jobs,python_source_updater as updater
from localharness.package_notice import read,write


class Continuation(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='helene-continuation-230-');self.addCleanup(self.temporary.cleanup)
        self.base=Path(self.temporary.name);self.root=self.base/'program';self.root.mkdir()
        self.tree=self.base/'data';self.tree.mkdir();write(self.root/'python-app.json',{'version':'2.3.0'})
        self.owner=SimpleNamespace(root=self.root,current=lambda:SimpleNamespace(tree=self.tree),stop=threading.Event())
        self.owner.update_cancelled=self.owner.stop.is_set

    def job(self,state='attention'):
        value={'format':'helene-desktop-update-request.v2','id':'d'*32,'state':state,'phase':'preparing',
            'program':str(self.root),'tree':str(self.tree),'from_version':'2.3.0','source_mode':'preserve'}
        jobs._save(self.tree,value);self.owner.update_job=value;return value

    def test_waiting_receipt_is_not_cancelled_when_observer_disappears(self):
        with patch('localharness.update_progress.status',return_value={'stage':'waiting_review','id':'same'}):
            self.assertEqual(updates.update_progress(self.owner),{'stage':'waiting_review','id':'same'})

    def test_attention_resume_keeps_same_identity_and_materials(self):
        value=self.job();value['staging']=str(self.base/'.helene-update-fixture')
        jobs._save(self.tree,value)
        resumed=jobs.request(self.tree,'resume',root=self.root,identity=value['id'])
        self.assertEqual(resumed['id'],value['id']);self.assertEqual(resumed['staging'],value['staging'])
        self.assertEqual(resumed['state'],'confirmed');self.assertIn('resume_requested',resumed)

    def test_explicit_cancel_is_durable_and_never_a_resumable_permission(self):
        value=self.job();jobs.request(self.tree,'cancel',root=self.root,identity=value['id'])
        self.assertTrue(jobs.cancelled(self.tree,value['id']))
        with self.assertRaisesRegex(ValueError,'Cancelled'):jobs.request(self.tree,'resume',root=self.root,identity=value['id'])

    def test_pause_holds_the_real_preparation_boundary_until_explicit_resume(self):
        value=self.job('paused');marker=self.tree/'memory/.control/autonomy-paused.json';write(marker,{'paused':True})
        progress=Mock();errors=[]
        def wait():
            try:updates._wait_pause(self.owner,progress)
            except BaseException as exc:errors.append(exc)
        thread=threading.Thread(target=wait);thread.start()
        try:
            time.sleep(.2);self.assertTrue(thread.is_alive())
            marker.unlink();value['state']='running';jobs._save(self.tree,value)
            thread.join(3);self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
            progress.phase.assert_called_once()
        finally:
            self.owner.stop.set();marker.unlink(missing_ok=True);value['state']='running';jobs._save(self.tree,value);thread.join(3)

    def test_cancel_interrupts_a_paused_job_without_removing_the_pause_intent(self):
        self.job('paused');marker=self.tree/'memory/.control/autonomy-paused.json';write(marker,{'paused':True})
        self.owner.stop.set()
        with self.assertRaises(InterruptedError):updates._wait_pause(self.owner)
        self.assertTrue(marker.is_file())

    def test_prepared_resume_revalidates_exact_preimages_without_preparing_again(self):
        new=self.base/'.helene-update-fixture/Helene';new.mkdir(parents=True)
        write(updater.prepared_install_path(new),{'retained':'exact prepared transaction'})
        with patch.object(updater,'validate_prepared',return_value={'bound':'same'}) as validate, \
             patch('localharness.source_admission.activate',side_effect=AssertionError('do not rebuild')):
            result=updater.prepare_install(self.root,new)
        self.assertEqual(result,{'bound':'same'});validate.assert_called_once_with(self.root.resolve(),new.resolve(),None)

    def test_private_ui_waiter_has_no_live_or_test_only_authority(self):
        self.assertFalse(hasattr(updates,'_wait_for_review'))


if __name__=='__main__':unittest.main()
