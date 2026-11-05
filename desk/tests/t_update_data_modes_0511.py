"""Actual migration copies/bindings: source policy never discards authored life."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import app_migration


class UpdateDataModes(unittest.TestCase):
    def test_clean_and_preserve_retain_memory_chats_settings_pause_and_originals(self):
        with tempfile.TemporaryDirectory(prefix='helene source policy synthetic ') as tmp:
            root=Path(tmp); old=root/'previous'; data=old/'data'; code=old/'authored-code'
            life={'memory/owner.json':b'{"synthetic":"authored memory"}',
                  'memory/groups/window.jsonl':b'{"text":"synthetic conversation"}\n',
                  'soul/SOUL.md':'Синтетическая конституция'.encode(),
                  'workspace/notes.md':b'synthetic authored work'}
            for name,raw in life.items():
                target=data/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
            code.mkdir();(code/'agent.py').write_bytes(b'authored_source=True\n')
            config={'tree':'data','code':'authored-code','runner':'','mode':'local',
                    'model':{'key':'synthetic-secret','model':'authored-model'},
                    'autonomy':{'paused':True},'owner_extension':{'keep':[1,2,3]}}
            (old/'helene.json').write_text(json.dumps(config),encoding='utf8')
            original=(old/'helene.json').read_bytes()
            for mode in ('clean','preserve'):
                with self.subTest(mode=mode):
                    new=root/mode
                    note=app_migration.prepare(old,new,root/(mode+'-data'),source_policy=mode)
                    actual=json.loads((new/'helene.json').read_text(encoding='utf8'))
                    for key in ('model','autonomy','owner_extension'):
                        self.assertEqual(actual[key],config[key])
                    selected=Path(actual['tree'])
                    for name,raw in life.items():
                        self.assertEqual((selected/name).read_bytes(),raw)
                        self.assertEqual((data/name).read_bytes(),raw)
                    self.assertEqual((old/'helene.json').read_bytes(),original)
                    self.assertEqual((code/'agent.py').read_bytes(),b'authored_source=True\n')
                    self.assertEqual(note['source_policy'],mode)


if __name__=='__main__':unittest.main(verbosity=2)
