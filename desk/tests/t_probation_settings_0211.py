"""Actual private authenticated Save; synthetic health/OS retirement only.

No live installation, provider request, model call or native UI acceptance.
The inert programme fixture still exercises real immutable payload guards.
"""
import copy,json,socket,sys,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
import psutil as _REAL_PSUTIL

sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).parent)]
from localharness import boot,session_host,portable_switch as switch,portable_retirement as retirement
from localharness import first_start,python_controller
from localharness.package_notice import read,write
import t_retirement_recovery_3110 as fixture

_REAL_SOCKET=socket.socket

class PrivateController(python_controller.Controller):
    def start(self):pass
    def close(self):self.stop.set()
    def status(self):return {'agents':{},'scope':'private config_save only; no workers'}
    def refresh(self):pass

class ProbationSettings(unittest.TestCase):
    def setUp(self):
        self.fx=fixture.Recovery('runTest');self.addCleanup(self.fx.doCleanups)
        if self._testMethodName=='test_legacy_optional_null_is_captured_and_normal_save_can_write_defaults':
            capture=retirement.capture
            def old_producer_with_null(program,*args,**kwargs):
                # Construct a legacy producer input BEFORE its strict capture,
                # never re-bless changed bytes from a running generation.
                config=read(program/'helene.json');config['self_review']=None;write(program/'helene.json',config)
                return capture(program,*args,**kwargs)
            with patch.object(retirement,'capture',side_effect=old_producer_with_null):self.fx.setUp()
        else:self.fx.setUp()
        self.program=self.fx.program;self.old=self.fx.old;self.value=self.fx.value
        self.journal=self.fx.journal;self.places=self.fx.fx.places
        self.before=self.sources()
        # The parent retirement fixture denies all OS authority. Restore only
        # actual loopback sockets and own PID/birth observation for this Host.
        self.enterContext(patch.object(socket,'socket',_REAL_SOCKET))
        self.enterContext(patch.dict(sys.modules,{'psutil':_REAL_PSUTIL}))
        self.enterContext(patch.object(boot,'project_brain',return_value='explicit private projection boundary'))
        self.host=session_host.Host(self.program,controller_factory=PrivateController)
        self.host_errors=[]
        def serve():
            try:self.host.run()
            except BaseException as error:self.host_errors.append(error)
        self.thread=threading.Thread(target=serve,name='private-probation-settings-host')
        self.thread.start();self.addCleanup(self.close_host)
        deadline=time.monotonic()+8
        while self.host.descriptor['state']!='running':
            if self.host_errors:raise self.host_errors[0]
            if time.monotonic()>deadline:self.fail('Private Host did not start')
            time.sleep(.01)
        self.client=session_host.SessionClient(self.program,self.host.descriptor,lambda *args:None,agent='main')
        self.addCleanup(self.client.close)

    def close_host(self):
        self.host.exit(owner_exit=False);self.thread.join(5)
        self.assertFalse(self.thread.is_alive());self.assertEqual(self.host_errors,[])

    def sources(self):
        return {folder:{p.relative_to(self.program/folder).as_posix():p.read_bytes()
                        for p in (self.program/folder).rglob('*') if p.is_file()}
                for folder in ('app','tree','runtime')}

    def save(self,change=None):
        loaded=self.client.invoke('config_load',{})
        value=copy.deepcopy(loaded['config'])
        if change:change(value)
        saved=self.client.invoke('config_save',{'config':json.dumps(value,ensure_ascii=False),'mtimeNs':loaded['mtime_ns']})
        self.assertTrue(saved['ok']);return value

    def validate_both(self):
        for name,action in [('prepared',lambda:switch.validate(self.old,self.program,self.value)),
                            ('retirement',lambda:retirement.validate(self.program,self.journal,self.places))]:
            with self.subTest(surface=name):action()
        self.assertEqual(self.sources(),self.before)

    def assert_rejected(self):
        for name,action in [('prepared',lambda:switch.validate(self.old,self.program,self.value)),
                            ('retirement',lambda:retirement.validate(self.program,self.journal,self.places))]:
            with self.subTest(surface=name),self.assertRaises((OSError,ValueError,RuntimeError)):action()
        self.assertTrue(all(path.exists() for path in self.fx.scenario.containers))
        self.assertFalse(any(action[0]=='remove' for action in self.fx.fx.backend.actions))

    def test_authenticated_save_without_semantic_changes_keeps_both_guards(self):
        path=self.program/'helene.json';before=path.read_bytes()
        self.save()
        self.assertNotEqual(path.read_bytes(),before,'Real Save must exercise its actual JSON rewrite')
        self.assertEqual(json.loads(before),json.loads(path.read_bytes()))
        self.validate_both()

    def test_authenticated_model_save_keeps_both_guards(self):
        self.save(lambda config:config['model'].update(model='private-owner-selected-model'))
        self.validate_both()

    def test_plain_json_reformat_keeps_meaning_and_does_not_require_a_save_receipt(self):
        path=self.program/'helene.json';value=json.loads(path.read_bytes())
        path.write_text(json.dumps(value,sort_keys=True,ensure_ascii=True,indent=4)+'\n',encoding='utf-8')
        self.validate_both()

    def test_normalized_review_defaults_and_phone_preferences_are_mutable(self):
        self.save(lambda config:config.update(self_review={},phone={'enabled':False,'mode':'automatic',
             'local_port':32199,'external':'https://private.invalid'}))
        self.validate_both()

    def test_save_between_payload_scan_and_frozen_settings_check_is_revalidated(self):
        entered=threading.Event();release=threading.Event();errors=[];original=switch.payload
        def scanned(*args,**kwargs):
            result=original(*args,**kwargs);entered.set()
            if not release.wait(4):raise RuntimeError('Private validation was not released')
            return result
        def validate():
            try:switch.validate(self.old,self.program,self.value)
            except BaseException as error:errors.append(error)
        with patch.object(switch,'payload',side_effect=scanned):
            worker=threading.Thread(target=validate,name='private-probation-read');worker.start()
            try:
                self.assertTrue(entered.wait(3))
                self.save(lambda config:config['model'].update(reasoning_effort='low'))
            finally:release.set();worker.join(4)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
        self.validate_both()

    def test_settings_save_while_retiring_rechecks_both_snapshots_and_commits(self):
        stop=self.fx.fx.backend.stop
        def save_during_stop(*args,**kwargs):
            result=stop(*args,**kwargs)
            self.save(lambda config:config['model'].update(model='saved-during-private-retirement'))
            return result
        with patch.object(self.fx.fx.backend,'stop',side_effect=save_during_stop):
            self.fx.audit._commit(self.journal)
        self.assertFalse(any(root.exists() for root in self.fx.scenario.containers))
        self.assertEqual(read(retirement.path(self.program))['state'],'completed')
        self.assertEqual(self.sources(),self.before)

    def test_data_binding_save_during_retirement_blocks_every_deletion(self):
        destination=self.fx.fx.base/'other-data';destination.mkdir()
        stop=self.fx.fx.backend.stop
        def change_binding(*args,**kwargs):
            result=stop(*args,**kwargs);self.save(lambda config:config.update(tree=str(destination)));return result
        with patch.object(self.fx.fx.backend,'stop',side_effect=change_binding),self.assertRaises(ValueError):
            self.fx.audit._commit(self.journal)
        self.assert_rejected()

    def test_no_settings_waiver_before_actual_handoff(self):
        note=read(self.program/'python-migration.json');note['state']='prepared';write(self.program/'python-migration.json',note)
        self.save(lambda config:config['model'].update(model='too-early'))
        self.assert_rejected()

    def test_unconfirmed_live_owner_cannot_authorize_mutable_settings(self):
        self.save(lambda config:config['model'].update(model='private-new-selection'))
        with patch.object(session_host,'process_state',return_value='unknown'):self.assert_rejected()

    def test_tree_code_and_runtime_selectors_remain_frozen_after_save(self):
        from localharness import probation_settings
        original=read(self.program/'helene.json')
        for key,value in [('tree',str(self.fx.fx.base/'other-data')),('code',str(self.program/'other-code')),
                          ('python','foreign-python'),('app','foreign-app.py'),('runner','foreign-runner.py'),
                          ('read_dotenv',True),('mode','remote')]:
            with self.subTest(key=key):
                write(self.program/'helene.json',original)
                self.save(lambda config:config.update({key:value}))
                with self.assertRaisesRegex(ValueError,'frozen'):
                    probation_settings.compare(self.program,self.value['trial_settings'],self.value['payload']['settings'])
                self.assert_rejected()
        write(self.program/'helene.json',original)

    def test_unknown_root_and_nested_options_are_not_mutable(self):
        original=read(self.program/'helene.json')
        for mutate in (lambda config:config.update(future_runtime={'executable':'unreviewed'}),
                       lambda config:config['model'].update(command='unreviewed')):
            write(self.program/'helene.json',original);self.save(mutate);self.assert_rejected()

    def test_config_roster_cannot_change_during_the_trial(self):
        self.save()
        extra=self.program/'agents/extra/helene.json';extra.parent.mkdir(parents=True)
        write(extra,read(self.program/'helene.json'));self.assert_rejected()

    def test_saved_settings_do_not_mask_changed_application_code_or_runtime(self):
        self.save(lambda config:config['model'].update(model='private-model'))
        for relative in ('app/current.py','tree/merged.py','runtime/python.exe'):
            with self.subTest(file=relative):
                path=self.program/relative;original=path.read_bytes();path.write_bytes(original+b'changed\n')
                self.assert_rejected();path.write_bytes(original)

    def test_missing_or_changed_original_document_cannot_be_reblessed(self):
        from localharness import probation_settings
        self.save(lambda config:config['model'].update(model='private-model'))
        proof=self.value['trial_settings'];digest=self.value['payload']['settings']['helene.json']
        path=probation_settings._baseline(self.program,proof['migration'],digest);original=path.read_bytes()
        path.write_bytes(b'{}');self.assert_rejected()
        path.write_bytes(original);path.unlink();self.assert_rejected()

    def test_one_unpaired_or_edited_receipt_supplies_no_approval(self):
        self.save(lambda config:config['model'].update(model='private-model'))
        prepared=copy.deepcopy(self.value);prepared['trial_settings']['settings']['helene.json']='0'*64
        with self.assertRaises(ValueError):switch.validate(self.old,self.program,prepared)
        journal=copy.deepcopy(self.journal);journal['trial_settings']['settings']['helene.json']='0'*64
        with self.assertRaises(ValueError):retirement.validate(self.program,journal,self.places)
        self.assertTrue(all(root.exists() for root in self.fx.scenario.containers))

    def test_rc22_raw_payload_is_unchanged_and_changed_legacy_input_cannot_be_adopted(self):
        from localharness import probation_settings
        original=self.fx.scenario.value
        self.assertNotIn('trial_settings',original)
        self.assertEqual(self.value['payload']['settings'],original['payload']['settings'])
        self.assertEqual(self.journal['payload'],original['payload'])
        self.save(lambda config:config['model'].update(model='private-model'))
        with self.assertRaisesRegex(ValueError,'Prelaunch'):
            probation_settings.capture(self.program,original['payload']['settings'],self.value['note']['id'])
        legacy=copy.deepcopy(self.value);legacy.pop('trial_settings')
        with self.assertRaisesRegex(ValueError,'payload changed'):switch.validate(self.old,self.program,legacy)

    def test_pause_stop_and_generation_checks_still_precede_deletion_after_save(self):
        from localharness import owner_stop
        self.save(lambda config:config['model'].update(model='private-model'))
        with patch.object(owner_stop,'stopped',return_value=True),self.assertRaises(InterruptedError):
            self.fx.audit._commit(self.journal)
        owner_stop.change_pause(self.fx.data,True,via='private settings trial test')
        # Use actual health for the pause read. The general retirement fixture
        # deliberately replaces health with a constant synthetic generation.
        with patch.object(retirement.time,'monotonic',return_value=300.),self.assertRaises(switch.TrialPaused):
            retirement.commit(self.program,self.journal,self.fx.audit._window(),places=self.places,backend=self.fx.fx.backend)
        owner_stop.change_pause(self.fx.data,False,via='private settings trial test')
        with self.assertRaises(switch.TrialRestart):
            self.fx.audit._commit(self.journal,identity='replacement-generation')
        self.assertTrue(all(root.exists() for root in self.fx.scenario.containers))
        self.assertFalse(any(action[0]=='remove' for action in self.fx.fx.backend.actions))

    def test_private_settings_read_retains_chunk_cancellation_and_closes_its_handle(self):
        from localharness import probation_settings,validation_read
        path=self.program/'helene.json';checks=[]
        def cancel():
            checks.append(True)
            if len(checks)==3:raise validation_read.ReadCancelled('private read revoked')
        with validation_read.scope(cancel),self.assertRaises(validation_read.ReadCancelled):probation_settings._raw(path)
        self.assertIsNone(validation_read.current())
        moved=path.with_suffix('.released');path.rename(moved);moved.rename(path)
        self.assertTrue(all(root.exists() for root in self.fx.scenario.containers))

    def test_unknown_nested_self_review_option_remains_frozen(self):
        self.save(lambda config:config.update(self_review={'after_update':False,'unknown_future_binding':'changed'}))
        self.assert_rejected()

    def test_all_existing_self_review_fields_accept_an_ordinary_authenticated_save(self):
        self.save(lambda config:config.update(self_review={'after_update':False,'after_self_edit':True,'depth':'deep',
             'models':['private-reviewer-a','private-reviewer-b'],'max_parallel':2,'max_iters':1}))
        self.validate_both()

    def test_legacy_optional_null_is_captured_and_normal_save_can_write_defaults(self):
        current=self.client.invoke('config_load',{})['config']
        self.assertIn('self_review',current);self.assertIsNone(current['self_review'])
        self.save(lambda config:config.update(self_review={}))
        self.assertIsInstance(read(self.program/'helene.json')['self_review'],dict)
        self.validate_both()

    def test_invalid_self_review_types_cannot_use_the_mutable_surface(self):
        original=read(self.program/'helene.json')
        for value in (False,[],0,'invalid',{'after_update':1},{'max_parallel':'2'},{'max_iters':-1},
                      {'depth':'unknown'},{'models':[{'unknown_future_binding':'changed'}]}):
            with self.subTest(value=value):
                current=copy.deepcopy(original);current['self_review']=value
                write(self.program/'helene.json',current);self.assert_rejected()

    def test_documented_typed_data_collections_are_mutable(self):
        def change(config):
            config['model']['keys']={'private-provider':'private-placeholder'}
            config['telegram']={'allowed_ids':['123',456],'proxy':{'enabled':False,'url':'https://private.invalid','key':'fixture'}}
            config['computer']={'enabled':False,'scopes':['observe','clipboard']}
            config['sandbox']={'enabled':True,'mounts':[{'path':str(self.fx.data),'access':'read','why':'private mount'}],
                               'mounts_denied':[{'path':str(self.fx.fx.base/'denied'),'why':'private denial'}]}
        self.save(change);self.validate_both()

    def test_unknown_structures_inside_known_options_are_not_opaque_mutable_data(self):
        original=read(self.program/'helene.json')
        changes=[lambda config:config['model'].update(keys={'private-provider':{'unknown_future_binding':'changed'}}),
            lambda config:config['model'].update(key={'unknown_future_binding':'changed'}),
            lambda config:config.update(telegram={'proxy':{'enabled':False,'unknown_future_binding':'changed'}}),
            lambda config:config.update(computer={'scopes':[{'unknown_future_binding':'changed'}]}),
            lambda config:config.update(sandbox={'mounts':[{'path':str(self.fx.data),'unknown_future_binding':'changed'}]}),
            lambda config:config['model'].update(temperature=float('nan'))]
        for index,change in enumerate(changes):
            with self.subTest(index=index):
                current=copy.deepcopy(original);change(current);write(self.program/'helene.json',current)
                self.assert_rejected()

if __name__=='__main__':unittest.main()
