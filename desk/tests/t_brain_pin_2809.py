"""A legacy model pin no longer overrides the agent's authored choice."""
import json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import boot, config_cli

class RetiredPin(unittest.TestCase):
    def test_old_pin_cannot_undo_authored_choice_but_new_selection_applies(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree=Path(tmp)
            cfg={'model':{'framework':'openai','provider':'chatgpt','model':'gpt-6-sol',
                         'base_url':'http://127.0.0.1:5011','pinned':True}}
            boot.project_brain(tree,cfg)
            path=tree/'memory/llm.json'
            current=json.loads(path.read_text(encoding='utf-8'))
            current['roles']['voice']['model']='gpt-6-astra'
            path.write_text(json.dumps(current),encoding='utf-8')
            boot.project_brain(tree,cfg)
            self.assertEqual(json.loads(path.read_text())['roles']['voice']['model'],'gpt-6-astra')
            cfg['model']['model']='gpt-6.1-sol'
            boot.project_brain(tree,cfg)
            self.assertEqual(json.loads(path.read_text())['roles']['voice']['model'],'gpt-6.1-sol')

    def test_save_removes_legacy_pin_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'helene.json'
            self.assertTrue(config_cli.save(path,json.dumps({'model':{'model':'sol','pinned':True}}),None)['ok'])
            self.assertEqual(json.loads(path.read_text())['model'],{'model':'sol'})

if __name__=='__main__':unittest.main()
