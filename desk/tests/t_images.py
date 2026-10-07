"""Image settings, voice credentials and scoped window output through real seams."""
import json,sys,tempfile,unittest
from pathlib import Path
DESK=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(DESK),str(DESK/'localharness')]
import boot
from localharness.transport import Desk

class Images(unittest.TestCase):
    def test_glm_voice_uses_separate_image_channel_and_persists_projection(self):
        cfg={'model':{'framework':'anthropic','base_url':'https://provider.invalid/anthropic','model':'glm-5.3','key':'voice-secret'},
             'relay':{'enabled':False,'port':5123,'key':'loop-key'},
             'images':{'enabled':True,'model':'gpt-image-2','quality':'high','size':'auto','background':'transparent'}}
        built=boot._brain_config(cfg)
        self.assertEqual(built['frameworks']['anthropic']['api_key'],'voice-secret')
        self.assertEqual(built['roles']['voice']['framework'],'anthropic')
        self.assertEqual(built['image_channel'],{'base_url':'http://127.0.0.1:5123','api_key':'loop-key'})
        self.assertEqual(built['images'],cfg['images'])
        merged=boot._merge_brain({'roles':{},'frameworks':{'openai':{'base_url':'https://text.invalid/v1','api_key':'text-key'}},'custom':{'keep':True}},built)
        self.assertEqual(merged['frameworks']['openai']['api_key'],'text-key')
        self.assertEqual(merged['custom'],{'keep':True})
        self.assertEqual(json.loads(json.dumps(merged))['images']['background'],'transparent')

    def test_disabled_images_do_not_borrow_provider_secret(self):
        built=boot._brain_config({'model':{'framework':'anthropic','model':'glm-5.3','key':'voice-secret'}})
        self.assertFalse(built['images']['enabled'])
        self.assertEqual(built['image_channel']['api_key'],'')

    def test_projection_preserves_authored_image_key_and_clears_its_own(self):
        cfg={'model':{'framework':'anthropic','model':'glm-5.3','key':'voice-secret'},'images':{'enabled':True}}
        built=boot._brain_config(cfg)
        current={'image_channel':{'base_url':'http://127.0.0.1:5011','api_key':'authored-loop','custom':'keep'}}
        merged=boot._merge_brain(current,built,{})
        self.assertEqual(merged['image_channel']['api_key'],'authored-loop')
        self.assertEqual(merged['image_channel']['custom'],'keep')
        self.assertEqual(current['image_channel']['api_key'],'authored-loop')
        managed=boot._brain_config({**cfg,'relay':{'key':'managed-loop'}})
        receipt=boot._projected_fields(managed)
        self.assertIn('api_key',receipt['_image_channel'])
        merged=boot._merge_brain(current,managed,{})
        self.assertEqual(merged['image_channel']['api_key'],'managed-loop')
        cleared=boot._merge_brain(merged,built,receipt)
        self.assertNotIn('api_key',cleared['image_channel'])
        self.assertEqual(cleared['image_channel']['custom'],'keep')

    def test_window_receipt_archives_image_metadata_for_existing_renderer(self):
        with tempfile.TemporaryDirectory() as folder:
            room=Desk(Path(folder),'window','Owner','Window')
            receipt=room.deliver('image caption',media_path='media/scopes/owner/new.png',media_kind='image')
            rows=[json.loads(line) for line in (Path(folder)/'memory/groups/window.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(rows[-1]['media_path'],'media/scopes/owner/new.png')
            self.assertEqual(rows[-1]['media_kind'],'image')
            self.assertIn('id 1',receipt)

    def test_new_skills_seed_and_factory_index_refresh_preserve_authored_files(self):
        with tempfile.TemporaryDirectory() as folder:
            tree=Path(folder);target=tree/'soul/skills/telegram-mtproto-raw.md';target.parent.mkdir(parents=True)
            target.write_text('my own adapted knowledge',encoding='utf-8')
            index=tree/'soul/skills/INDEX.md';index.write_text('my authored index',encoding='utf-8')
            boot._seed_kit(tree,{})
            boot.refresh_kit(tree,{})
            self.assertEqual(target.read_text(encoding='utf-8'),'my own adapted knowledge')
            self.assertEqual(index.read_text(encoding='utf-8'),'my authored index')
            self.assertTrue((tree/'soul/skills/telegram-search-history.md').is_file())

if __name__=='__main__':unittest.main()
