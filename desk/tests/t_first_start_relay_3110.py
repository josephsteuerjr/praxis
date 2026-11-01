"""Full first-start/window progress boundary with legacy relay configurations.

OS retirement is fake; real preparation, relay selection, configuration/data
transfer and the window's progress consumer run together.
"""
import json,os,sys,threading,unittest
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).parent)]
from localharness import first_start,standalone_relay,windows_first_start
from first_start_window import FirstStartWindow
import t_first_start_3010 as fixtures


class RelayWindow(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.FirstStart('test_whole_contract_preserves_agent_moves_data_and_clears_every_old_copy')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.old,self.data=self.fixture.old()
        # A native per-user installation is admitted by registration; portable
        # data fallback deliberately does not walk Local/Programs anymore.
        self.fixture.backend.registered=lambda _places:[self.old]
        self.history=(self.data/'memory/groups/window.jsonl').read_bytes()
        self.window=FirstStartWindow.__new__(FirstStartWindow)
        self.window.root=self.fixture.root;self.window.places=self.fixture.places
        self.window.value={};self.window.value_lock=threading.Lock()
        self.events=[]
        publish=self.window.publish
        def observe(row):
            publish(row);self.events.append(row)
        self.window.publish=observe
    def configure(self,**changes):
        value=first_start.load(self.old/'helene.json');value.update(changes)
        (self.old/'helene.json').write_text(json.dumps(value),encoding='utf8')
    def install(self,program,progress=None):
        self.assertTrue(self.old.exists(),'Relay preparation must precede old programme removal')
        target=Path(program)/'relay-product'/('praxis-relay-headless.exe' if os.name=='nt' else 'praxis-relay-headless')
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'current pinned relay fixture')
        if progress:progress({'stage':'downloading','bytes':8,'total_bytes':8})
        return {'installed':True,'path':str(target)}
    def test_existing_enabled_relay_reaches_real_window_ready_and_preserves_data(self):
        self.configure(relay={'enabled':True})
        with patch.object(windows_first_start,'Backend',return_value=self.fixture.backend),patch.object(standalone_relay,'install',side_effect=self.install):
            self.window.prepare()
        self.assertEqual(self.window.status()['stage'],'ready',self.window.status())
        self.assertTrue(all(isinstance(row,dict) for row in self.events))
        self.assertTrue(all(row.get('label') for row in self.events))
        self.assertIn('preparing_relay',[row['stage'] for row in self.events])
        self.assertEqual((self.fixture.places.data/'memory/groups/window.jsonl').read_bytes(),self.history)
        self.assertFalse(self.old.exists())


class SubscriptionCreation(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.FirstStart('test_first_install_has_one_data_home_and_minimal_creation_saves_private_credentials')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.fixture.prepare();first_start.ensure_config(self.fixture.root,places=self.fixture.places)

    def create(self,**changes):
        values=dict(name='New agent',owner='Owner',provider='chatgpt',model='chosen-model',key='')
        values.update(changes)
        return first_start.create_agent(self.fixture.root,values,places=self.fixture.places)

    def install(self,program,progress=None):
        self.assertFalse(first_start.load(self.fixture.root/'helene.json')['setup_complete'])
        path=standalone_relay.executable(program);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'pinned fixture')
        if progress:progress({'stage':'downloading','bytes':8,'total_bytes':8})
        return {'installed':True,'path':str(path)}

    def test_subscription_installs_before_creation_and_stays_paused_until_login(self):
        from deskd.autonomy import is_paused
        with patch.object(standalone_relay,'install',side_effect=self.install) as install:
            self.create(key='never-forward-an-api-key',base_url='https://unused.invalid')
        install.assert_called_once()
        config=first_start.load(self.fixture.root/'helene.json')
        self.assertTrue(config['setup_complete']);self.assertTrue(config['relay']['enabled'])
        self.assertEqual(config['model']['key'],config['relay']['key'])
        self.assertTrue(config['model']['key'].startswith('sk-frame-'))
        self.assertNotIn('never-forward-an-api-key',json.dumps(config))
        self.assertEqual(config['model']['base_url'],f"http://127.0.0.1:{config['relay']['port']}")
        self.assertTrue(config['onboarding']['subscription_login']);self.assertTrue(is_paused(self.fixture.places.data))
        self.assertTrue(first_start.agent_exists(self.fixture.places.data))

    def test_failed_install_does_not_publish_an_agent_and_retry_succeeds(self):
        with patch.object(standalone_relay,'install',side_effect=OSError('offline')):
            with self.assertRaisesRegex(OSError,'offline'):self.create()
        self.assertFalse(first_start.load(self.fixture.root/'helene.json')['setup_complete'])
        self.assertFalse(first_start.agent_exists(self.fixture.places.data))
        with patch.object(standalone_relay,'install',side_effect=self.install):self.create()
        self.assertTrue(first_start.agent_exists(self.fixture.places.data))

    def test_api_creation_never_downloads_relay_and_missing_key_stays_a_form_error(self):
        with patch.object(standalone_relay,'install') as install:
            with self.assertRaisesRegex(ValueError,'ключ'):self.create(provider='openai')
            self.create(provider='openai',key='private-test-key')
        install.assert_not_called()
        config=first_start.load(self.fixture.root/'helene.json')
        self.assertFalse(config['relay']['enabled']);self.assertEqual(config['model']['key'],'private-test-key')

    def test_prepared_subscription_profile_installs_missing_component_before_window_ready(self):
        with patch.object(standalone_relay,'install',side_effect=self.install):self.create()
        standalone_relay.executable(self.fixture.root).unlink()
        window=FirstStartWindow.__new__(FirstStartWindow)
        window.root=self.fixture.root;window.places=self.fixture.places
        window.value={};window.value_lock=threading.Lock()
        def restore_install(program,progress=None):
            path=standalone_relay.executable(program);path.write_bytes(b'current pinned component')
            if progress:progress({'stage':'downloading','bytes':8,'total_bytes':8})
        with patch.object(windows_first_start,'Backend',return_value=self.fixture.backend),patch.object(standalone_relay,'install',side_effect=restore_install) as install:
            window.prepare()
        install.assert_called_once();self.assertEqual(window.status()['stage'],'ready',window.status())

    def test_new_subscription_opens_login_but_normal_api_launch_keeps_its_start_page(self):
        from types import SimpleNamespace
        from localharness import python_controller,agents
        with patch.object(standalone_relay,'install',side_effect=self.install):self.create()
        target=SimpleNamespace(name='New agent',id='main',config=self.fixture.root/'helene.json',
            tree=self.fixture.places.data,port=8094)
        owner=python_controller.Controller.__new__(python_controller.Controller);owner.root=self.fixture.root
        with patch.object(owner,'current',return_value=target),patch.object(agents,'roster',return_value=[]):
            self.assertTrue(owner.channel()['needs_subscription_login'])
            auth=target.tree/'relay/local_auth/accounts/primary/auth.json'
            auth.parent.mkdir(parents=True);auth.write_text('{"test":"stored local login"}')
            self.assertFalse(owner.channel()['needs_subscription_login'])
            config=first_start.load(target.config);config.pop('onboarding');config['relay']['enabled']=False
            target.config.write_text(json.dumps(config))
            auth.unlink()
            self.assertFalse(owner.channel()['needs_subscription_login'])


if __name__=='__main__':unittest.main()
