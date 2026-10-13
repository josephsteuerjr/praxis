"""No model, network, workers or installation; actual archive watcher and folding contracts."""
import datetime,json,os,sys,tempfile,threading,time,types,unittest
from pathlib import Path
from unittest import mock
DESK=Path(__file__).resolve().parents[1];sys.path[:0]=[str(DESK),str(DESK/'localharness')]
from deskd import control
import deskapp,runner

class Operation(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.ctl=self.root/'memory/.control';self.ctl.mkdir(parents=True);self.st=self.root/'memory/.state';self.st.mkdir()
    def write(self,path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value),encoding='utf8')
    def test_request_waiting_is_not_running_and_pause_is_preserved(self):
        control.fold_request(self.root,'window');(self.ctl/'autonomy-paused.json').write_text('{}')
        state=control.fold_state(self.root,'window')
        self.assertEqual(state['operation']['state'],'queued');self.assertTrue(state['paused'])
        self.assertIn('возобновления',state['operation']['note'])
    def test_old_processing_from_previous_runner_is_interrupted_without_deleting_evidence(self):
        asked={'id':'old','room':'window','at':'2026-01-01T00:00:00Z'}
        self.write(self.ctl/'fold.processing.json',asked);self.write(self.st/'fold-receipt.json',{**asked,'state':'running','pid':111})
        self.write(self.ctl/'desk_inbox/.reader.json',{'pid':222,'at':time.time(),'fold':None})
        state=control.fold_state(self.root,'window');self.assertEqual(state['operation']['state'],'interrupted')
        self.assertTrue((self.ctl/'fold.processing.json').is_file());self.assertEqual(state['receipt']['state'],'running')
    def test_long_fold_is_live_only_for_exact_current_reader_and_request(self):
        asked={'id':'active','room':'window','at':'2026-01-01T00:00:00Z'}
        self.write(self.ctl/'fold.processing.json',asked);self.write(self.st/'fold-receipt.json',{**asked,'state':'running','pid':222})
        reader=self.ctl/'desk_inbox/.reader.json'
        self.write(reader,{'pid':222,'at':time.time(),'fold':{'id':'active','room':'window'}})
        self.assertEqual(control.fold_state(self.root,'window')['operation']['state'],'running')
        self.assertIsNone(control.fold_state(self.root,'other')['operation'])
        self.write(reader,{'pid':222,'at':time.time()-60,'fold':{'id':'active','room':'window'}})
        self.assertEqual(control.fold_state(self.root,'window')['operation']['state'],'unconfirmed')
    def test_same_room_second_request_cannot_hide_first_live_fold(self):
        old={'id':'first','room':'window','at':'2026-01-01T00:00:00Z'}
        self.write(self.ctl/'fold.processing.json',old);control.fold_request(self.root,'window')
        self.write(self.ctl/'desk_inbox/.reader.json',{'pid':222,'at':time.time(),'fold':{'id':'first','room':'window'}})
        self.assertEqual(control.fold_state(self.root,'window')['operation']['state'],'running')
    def test_legacy_running_receipt_never_certifies_current_execution(self):
        asked={'id':'old','room':'window','at':'2026-01-01T00:00:00Z'}
        self.write(self.ctl/'fold.processing.json',asked);self.write(self.st/'fold-receipt.json',{**asked,'state':'running'})
        self.write(self.ctl/'desk_inbox/.reader.json',{'pid':222,'at':time.time()})
        self.assertEqual(control.fold_state(self.root,'window')['operation']['state'],'unconfirmed')
    def test_new_claim_waits_for_first_heartbeat(self):
        asked={'id':'new','room':'window','at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'pid':222,'state':'running'}
        self.write(self.ctl/'fold.processing.json',asked);self.write(self.st/'fold-receipt.json',asked)
        self.write(self.ctl/'desk_inbox/.reader.json',{'pid':222,'at':time.time(),'fold':None})
        self.assertEqual(control.fold_state(self.root,'window')['operation']['state'],'starting')
    def test_malformed_reader_timestamp_does_not_claim_live(self):
        self.write(self.ctl/'fold.processing.json',{'id':'old','room':'window'})
        self.write(self.ctl/'desk_inbox/.reader.json',{'pid':222,'at':{},'fold':{'id':'old','room':'window'}})
        self.assertEqual(control.fold_state(self.root,'window')['operation']['state'],'unconfirmed')

class PassiveWatcher(unittest.TestCase):
    def test_append_without_model_or_run_emits_archive_change_and_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);groups=root/'memory/groups';groups.mkdir(parents=True)
            archive=groups/'fixture.jsonl';archive.write_text('{"text":"first"}\n')
            events=[];ticks=[0]
            def step(_):
                ticks[0]+=1
                if ticks[0]==1:archive.write_text(archive.read_text()+'{"text":"new without wake"}\n')
                elif ticks[0]==2:archive.unlink()
                else:raise StopIteration
            with mock.patch.object(deskapp,'_SSE_CLIENTS',{object()}),mock.patch.object(deskapp.readers,'tree',return_value=root),\
                 mock.patch.object(deskapp.readers,'health',return_value={'alarms':[]}),mock.patch.object(deskapp.readers,'list_runs',return_value=[]),\
                 mock.patch.object(deskapp,'_flush_device_seen'),mock.patch.object(deskapp,'_broadcast',side_effect=lambda _,e:events.append((ticks[0],e))),\
                 mock.patch.object(deskapp.time,'sleep',side_effect=step):
                with self.assertRaises(StopIteration):deskapp._watcher(None)
            self.assertEqual([tick for tick,e in events if e['t']=='chats'],[0,1,2])
            self.assertFalse(any(e['t']=='run' for _,e in events))

class Worker(unittest.TestCase):
    def test_archive_failure_preserves_old_claim_and_new_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);ctl=root/'memory/.control';ctl.mkdir(parents=True)
            before=b'{"id":"old","room":"window"}'
            (ctl/'fold.processing.json').write_bytes(before)
            control.fold_request(root,'window');request=(ctl/'fold.json').read_bytes();life=mock.Mock()
            with mock.patch.object(runner,'_life',life),mock.patch.object(runner.owner_stop,'paused',return_value=False),\
                 mock.patch('shutil.copy2',side_effect=PermissionError('fixture denied')):
                runner._fold_tick(root)
            self.assertEqual((ctl/'fold.processing.json').read_bytes(),before)
            self.assertEqual((ctl/'fold.json').read_bytes(),request);life.fold_now.assert_not_called()
    def test_unreadable_claim_is_retained_without_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);ctl=root/'memory/.control';ctl.mkdir(parents=True)
            before=b'{broken request';(ctl/'fold.json').write_bytes(before);life=mock.Mock()
            with mock.patch.object(runner,'_life',life),mock.patch.object(runner.owner_stop,'paused',return_value=False):runner._fold_tick(root)
            self.assertEqual((ctl/'fold.processing.json').read_bytes(),before);life.fold_now.assert_not_called()
    def test_pause_never_claims_or_calls_fold(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);control.fold_request(root,'window');life=mock.Mock()
            with mock.patch.object(runner,'_life',life),mock.patch.object(runner.owner_stop,'paused',return_value=True):runner._fold_tick(root)
            self.assertTrue((root/'memory/.control/fold.json').is_file());life.fold_now.assert_not_called()
    def test_retry_preserves_orphan_and_previous_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);ctl=root/'memory/.control';ctl.mkdir(parents=True);st=root/'memory/.state';st.mkdir()
            before=b'{"id":"old","room":"window"}'
            receipt=b'{"id":"old","room":"window","state":"running"}'
            (ctl/'fold.processing.json').write_bytes(before);(st/'fold-receipt.json').write_bytes(receipt)
            control.fold_request(root,'window');life=types.SimpleNamespace(fold_now=lambda _: {'folded':1})
            with mock.patch.object(runner,'_life',life),mock.patch.object(runner.owner_stop,'paused',return_value=False),\
                 mock.patch.object(runner,'_FOLD_BUSY',[False]),mock.patch.object(runner,'_FOLD_ACTIVE',[None]):
                runner._fold_tick(root)
                deadline=time.monotonic()+3
                while runner._FOLD_BUSY[0] and time.monotonic()<deadline:time.sleep(.01)
                self.assertFalse(runner._FOLD_BUSY[0]);self.assertIsNone(runner._FOLD_ACTIVE[0])
            history=list((st/'fold-history').iterdir());self.assertEqual(len(history),1)
            self.assertEqual((history[0]/'request.json').read_bytes(),before);self.assertEqual((history[0]/'receipt.json').read_bytes(),receipt)
            self.assertEqual(json.loads((st/'fold-receipt.json').read_text())['pid'],os.getpid())

if __name__=='__main__':unittest.main()
