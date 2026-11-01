"""Configured install binding before bounded Local data fallback."""
import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import first_start as start

class Discovery(unittest.TestCase):
    def test_explicit_program_container_bindings_and_only_top_level_raw_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            profile=Path(directory)/'User';local=profile/'AppData/Local';local.mkdir(parents=True)
            roaming=profile/'AppData/Roaming';roaming.mkdir();pf=Path(directory)/'PF';pf.mkdir()
            root=profile/'Downloads/Helene';root.mkdir(parents=True)
            places=start.Places(profile,(local,roaming),(pf,),root.parent)
            def data(path):
                (path/'memory/groups').mkdir(parents=True)
                (path/'memory/groups/window.jsonl').write_text('{"text":"history"}\n')
                return path
            loose=data(local/'Helene-Data/latest')
            ignored=data(local/'Programs/Helene unbound/data')
            installed=data(local/'Programs/Helene installed/data')
            (installed.parent/'helene.json').write_text(json.dumps({'mode':'local','tree':'data','setup_complete':True}))
            outside=data(local/'Unrelated Vendor/Helene personal notes')
            roots,_=start.discover(root,places);configs=start.configurations(roots,places)
            found=start.data_sources(roots,configs,places)
            self.assertIn(loose,found);self.assertIn(installed,found)
            self.assertNotIn(ignored,found);self.assertNotIn(outside,found)
            self.assertGreater(start._score(installed,configs),start._score(loose,configs))

if __name__=='__main__':unittest.main()
