"""Owner pause/resume without models, live credentials, network or subprocesses."""
import datetime,json,os,sys,tempfile,time,types,unittest
from pathlib import Path
from unittest import mock
DESK=Path(__file__).resolve().parents[1];sys.path[:0]=[str(DESK),str(DESK/'localharness'),str(DESK.parent/'praxis')]
sys.path.insert(0,str(Path(os.environ.get('HELENE_TREE_SRC') or DESK.parent/'helene/core')))
import owner_stop,control_watch,botapi
from deskd import autonomy

class OwnerPause(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.ctl=self.root/'memory/.control';self.ctl.mkdir(parents=True)
        self.started='fixture-boot';self.put('desk_inbox/.reader.json',{'pid':os.getpid(),'at':time.time(),'started':self.started,'autonomy':{'protocol':1,'paused':False,'stopped':False}})
        self.stop=mock.patch.object(owner_stop,'stopped',return_value=False);self.stop.start();self.addCleanup(self.stop.stop);owner_stop.configure(self.root)
        self.manager=mock.Mock();self.manager.list_runs.return_value=[]
    def put(self,name,value):
        path=self.ctl/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
    def apply(self,before=None):return autonomy.consume(self.root,self.manager,['running'],self.started,before)
    def test_pause_resume_proof_and_model_delivery(self):
        asked=autonomy.request(self.root,'pause');self.assertTrue(asked['ok']);self.assertFalse(autonomy.state(self.root)['paused'])
        receipt=self.apply();self.assertTrue(receipt['done']);self.assertEqual(receipt['id'],asked['request']['id']);self.assertTrue(owner_stop.paused())
        with control_watch.delivery(lambda:self.manager,'') as allowed:self.assertFalse(allowed)
        callback=mock.Mock();autonomy.request(self.root,'resume');self.assertTrue(self.apply(callback)['done']);callback.assert_called_once();self.assertFalse(owner_stop.paused())
    def test_restart_and_seed_stop_never_unpause(self):
        owner_stop.pause();asked=autonomy.request(self.root,'resume');self.started='new-boot'
        self.assertFalse(self.apply()['done']);self.assertTrue(owner_stop.paused())
        self.started='fixture-boot';autonomy.request(self.root,'resume')
        with mock.patch.object(owner_stop,'stopped',return_value=True):self.assertFalse(self.apply()['done'])
        self.assertTrue(owner_stop.paused())
    def test_stale_legacy_or_missing_reader_has_no_mutation(self):
        self.put('desk_inbox/.reader.json',{'pid':os.getpid(),'at':time.time()-50,'started':self.started,'autonomy':{'protocol':1}})
        self.assertFalse(autonomy.request(self.root,'pause')['ok']);self.assertFalse((self.ctl/'autonomy-request.json').exists())
        self.put('desk_inbox/.reader.json',{'pid':os.getpid(),'at':time.time()});self.assertFalse(autonomy.state(self.root)['supported'])
    def test_old_resume_loses_to_new_pause(self):
        owner_stop.pause();autonomy.request(self.root,'resume');os.replace(self.ctl/'autonomy-request.json',self.ctl/'autonomy.processing.json')
        autonomy.request(self.root,'pause');self.assertFalse(self.apply()['done']);self.assertTrue(owner_stop.paused());self.assertTrue(self.apply()['done'])
    def test_only_preexisting_runs_cancel_and_other_tree_keeps_running(self):
        old=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(seconds=2)).isoformat();future=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=2)).isoformat()
        self.manager.list_runs.return_value=[{'id':'old','created_at':old},{'id':'future','created_at':future}]
        autonomy.request(self.root,'pause')
        with mock.patch('process_scope.cancel',return_value={'requested':0,'failures':[]}) as cancel:self.assertTrue(self.apply()['done'])
        cancel.assert_called_once_with('old');self.assertFalse(autonomy.state(self.root/'other')['paused'])
    def test_failed_process_stop_is_visible_while_pause_blocks_new_work(self):
        self.manager.list_runs.return_value=[{'id':'old','created_at':'2026-01-01T00:00:00Z'}];autonomy.request(self.root,'pause')
        with mock.patch('process_scope.cancel',return_value={'requested':1,'failures':['fixture permission denied']}):receipt=self.apply()
        self.assertTrue(owner_stop.paused());self.assertTrue(receipt['done']);self.assertTrue(receipt['failed']);self.assertIn('не подтверждена',receipt['note'])
    def test_receipt_failure_keeps_claim_and_resume_does_not_discard_new_wakes_twice(self):
        owner_stop.pause();autonomy.request(self.root,'resume');callback=mock.Mock();write=autonomy._write
        def fail_final(path,data):
            if data.get('done'):raise OSError('fixture receipt failure')
            return write(path,data)
        with mock.patch.object(autonomy,'_write',side_effect=fail_final):
            with self.assertRaises(OSError):self.apply(callback)
        self.assertTrue((self.ctl/'autonomy.processing.json').exists());self.assertFalse(owner_stop.paused());self.assertTrue(self.apply(callback)['done']);callback.assert_called_once()

class TelegramControl(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.bot=botapi.BotTransport(types.SimpleNamespace(),self.root,None,{'agent':{'name':'Fixture'},'telegram':{'owner_id':'17'}})
        self.bot.username='fixturebot';self.bot.me={'id':91};self.bot.client=mock.Mock();self.bot.autonomy_control=mock.Mock(return_value={'ok':True,'note':'requested'})
        self.bot.rooms.record=mock.Mock();self.bot.rooms.describe=mock.Mock();self.bot.contacts.note=mock.Mock();self.bot.on_incoming=mock.Mock()
        self.patch=mock.patch.object(owner_stop,'paused',return_value=True);self.patch.start();self.addCleanup(self.patch.stop)
    def send(self,text,sender=17,date=None,**update):
        msg={'message_id':4,'date':time.time() if date is None else date,'text':text,'from':{'id':sender,'first_name':'Fixture'},'chat':{'id':17,'type':'private'}}
        self.bot._ingest({'message':msg,**update})
    def test_fresh_owner_command_bypasses_pause_without_model_wake(self):
        self.send('/resume@fixturebot');self.bot.autonomy_control.assert_called_once_with('resume');self.bot.client.call.assert_called_once();self.bot.on_incoming.assert_not_called();self.assertFalse(self.bot._pending)
    def test_nonowner_wrongbot_history_catchup_or_old_control_is_inert(self):
        for text,sender,flags,date in [('/pause',8,{},None),('/pause@anotherbot',17,{},None),('/resume',17,{'_history':True},None),('/resume',17,{'_catchup':True},None),('/resume',17,{},time.time()-121)]:self.send(text,sender,date,**flags)
        self.bot.autonomy_control.assert_not_called();self.bot.client.call.assert_not_called()
    def test_paused_messages_still_archive_without_accumulating_wakes(self):
        self.send('new mention');self.bot.rooms.record.assert_called_once();self.bot.on_incoming.assert_not_called();self.assertFalse(self.bot._pending)
    def test_discard_keeps_archive_and_removes_pre_pause_wakes(self):
        self.bot._enqueue('17');self.bot.discard_pending();self.assertFalse(self.bot._pending);self.bot.rooms.record.assert_not_called()
    def test_duplicate_control_after_poll_replay_cannot_clear_newer_pause(self):
        self.send('/resume');self.send('/resume');self.bot.autonomy_control.assert_called_once_with('resume');self.bot.client.call.assert_called_once()

if __name__=='__main__':unittest.main()
