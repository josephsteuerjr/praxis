"""First launch with legacy relay settings and prerequisite retirement order."""
from pathlib import Path
import copy,json,os,sys,tempfile,unittest
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).parent)]
from localharness import standalone_relay as relay,desktop_supervisor as supervisor
from localharness import windows_installation as installation,migration_lifecycle as lifecycle,source_update_host
from localharness.package_notice import read,write
from localharness.update_progress import Progress
import t_migration_clean_windows_2810 as fixture


class MigrationRelay(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='migration-relay-');self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.old,self.new=fixture.CleanMigration().fixture(self.root)
        self.events=[]

    def install(self,current,progress):
        self.assertTrue((self.old/'code/agent.py').is_file(),'prerequisite must precede old-code removal')
        self.events.append('current-pinned-install')
        target=Path(current)/'relay-product'/('praxis-relay-headless.exe' if os.name=='nt' else 'praxis-relay-headless')
        target.parent.mkdir(parents=True);target.write_bytes(b'current release fixture')
        progress({'stage':'downloading','bytes':8,'total_bytes':8})
        return {'installed':True,'path':str(target)}

    def test_text_and_image_settings_prepare_one_current_component_without_touching_old(self):
        values=[{'relay':{'enabled':True,'key':'owner-key'}},{'images':{'enabled':True}},
                {'mode':'remote','relay':{'enabled':True,'executable':'not-a-local-engine'}}]
        original=copy.deepcopy(values);progress=[]
        with patch.object(relay,'install',side_effect=self.install) as install:
            actual=relay.prepare_migration(self.old,self.new,values,progress.append)
        self.assertEqual(install.call_count,1);self.assertEqual(actual,original);self.assertEqual(values,original)
        self.assertEqual(progress[-1],{'stage':'preparing_relay','label':'Подготовка подключения','bytes':8,'total_bytes':8})
        self.assertTrue(all(isinstance(row,dict) and row.get('label') for row in progress))

    def test_internal_executable_rebinds_to_current_while_independent_external_stays(self):
        external=self.root/'independent-relay';external.write_bytes(b'external owner')
        values=[{'relay':{'enabled':True,'executable':str(self.old/'helene-relay.exe')}},
                {'relay':{'enabled':True,'executable':str(external)}}]
        with patch.object(relay,'install',side_effect=self.install):
            actual=relay.prepare_migration(self.old,self.new,values)
        self.assertNotIn('executable',actual[0]['relay'])
        self.assertEqual(actual[1]['relay']['executable'],str(external.resolve()))
        self.assertIn('executable',values[0]['relay'])

    def test_disabled_and_remote_settings_never_download(self):
        with patch.object(relay,'install') as install:
            relay.prepare_migration(self.old,self.new,[{'relay':{'enabled':False}},
                {'mode':'remote','relay':{'enabled':True}}])
        install.assert_not_called()

    def test_missing_external_executable_refuses_before_any_install(self):
        with patch.object(relay,'install') as install:
            with self.assertRaises(FileNotFoundError):
                relay.prepare_migration(self.old,self.new,[{'relay':{'enabled':True,'executable':str(self.root/'missing')}}])
        install.assert_not_called();self.assertTrue((self.old/'code/agent.py').is_file())

    def test_failed_prerequisite_never_stops_or_removes_old_programme(self):
        value=read(self.old/'helene.json');value['relay']={'enabled':True};write(self.old/'helene.json',value)
        progress=Progress(self.root/'.helene-update-relay.progress.json','relay',self.new)
        with patch.object(source_update_host,'verified_runtime',return_value={}), \
             patch.object(relay,'install',side_effect=OSError('offline')), \
             patch.object(lifecycle,'stop') as stop,patch.object(lifecycle,'uninstaller_for',create=True) as uninstall, \
             patch.object(lifecycle,'remove_program') as remove:
            with self.assertRaisesRegex(OSError,'offline'):
                installation.run(self.new,self.new,old=self.old,launch=False,progress=progress)
        stop.assert_not_called();uninstall.assert_not_called();remove.assert_not_called()
        self.assertFalse((self.new/'helene.json').exists())
        self.assertTrue((self.old/'code/agent.py').exists())
        self.assertEqual(progress.value['stage'],'preparing_relay')

    def test_ordinary_window_can_open_without_relay_and_only_text_runner_is_blocked(self):
        cfg=self.new/'helene.json'
        value={'mode':'local','python':sys.executable,'app':'app/deskapp.py',
            'runner':'app/deskapp.py','setup_complete':True,'relay':{'enabled':True}}
        write(cfg,value)
        tree,specs=supervisor._specs(cfg)
        self.assertFalse(specs['main:channel'].unavailable)
        self.assertTrue(specs['main:relay'].unavailable);self.assertTrue(specs['main:runner'].unavailable)
        with self.assertRaises(ValueError):
            supervisor.relay_spec(cfg,value,tree,Path(sys.executable),install_base=self.new)
        value.update(relay={'enabled':False},images={'enabled':True});write(cfg,value)
        _,image_specs=supervisor._specs(cfg)
        self.assertFalse(image_specs['main:runner'].unavailable)
        supervisor._desktop_status(tree,'a'*32,1,None,{'main':(tree,specs)},{})
        status=read(tree/'.python-desktop-status')['agents']['main']
        self.assertEqual(set(status['problems']),{'relay','runner'});self.assertEqual(status['pids'],{})
        self.install(self.new,lambda _:None)
        _,ready=supervisor._specs(cfg)
        self.assertFalse(ready['main:relay'].unavailable)


if __name__=='__main__':unittest.main()
