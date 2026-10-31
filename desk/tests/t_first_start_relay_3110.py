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


if __name__=='__main__':unittest.main()
