"""Fresh project scaffolds and cached code maps keep authorship and language boundaries."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import frame_language as fl
import workshop

RAW='literal Русский 中文 {v1} <script>inert</script>'

class WorkshopProjectLanguage(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        for name,value in [('BASE',self.root),('REPO',self.root/'code'),('PROJECTS',self.root/'workspace/projects')]:self.enterContext(patch.object(workshop,name,value))
        self.enterContext(patch.object(workshop,'_MAP_CACHE',{'key':None,'text':''}))
    def settings(self,ui,language):return patch.object(fl,'settings',return_value={'lang':ui,'agent':{'frame_language':language}})
    def snapshot(self):return {str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}

    def test_actual_git_project_scaffold_preserves_brief_and_old_projects16axes(self):
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                name='project-'+ui+'-'+language
                with self.settings(ui,language),patch.object(workshop,'_git',wraps=workshop._git) as git:
                    answer=workshop.project_create(name,RAW);self.assertEqual(answer,fl.text('workshop.project.21',v1=name))
                    self.assertEqual(git.call_count,2)
                    folder=workshop.PROJECTS/name;self.assertTrue((folder/'.git').is_dir())
                    self.assertEqual((folder/'README.md').read_text(encoding='utf8'),fl.text('workshop.project.19',v1=name,v3=RAW,v5=workshop.praxis_time.day_key()))
                    before=self.snapshot();answer=workshop.project_create(name,'different');self.assertEqual(answer,fl.text('workshop.project.18',v1=name,v3=name));self.assertEqual(self.snapshot(),before)
                    self.assertIn(name,workshop.project_list());self.assertIn('##',workshop.project_status(name))

    def test_actual_outline_keeps_symbol_names_docstrings_and_source_bytes16axes(self):
        (self.root/'workspace').mkdir();path=self.root/'workspace/sample.py';raw=('"""'+RAW+'"""\nclass Literal:\n    """'+RAW+'"""\n    def exact(self):\n        return 1\n\ndef outside():\n    return 2\n').encode();path.write_bytes(raw);before=self.snapshot()
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with self.settings(ui,language):
                    answer=workshop.code_outline('workspace/sample.py');self.assertIn('Literal',answer);self.assertIn('exact',answer);self.assertIn(fl.text('workshop.project.57',v0='class',v2='Literal',v4=2),answer)
                    mapped=workshop._map_for_root(path.parent);self.assertIn(RAW,mapped);self.assertIn('class Literal :2',mapped);self.assertIn('def exact :4',mapped)
                    after=self.snapshot()
                    # Reading records its existing hands receipt; authored files remain exact.
                    self.assertIsNotNone(after.pop('workspace/memory/.state/hands.jsonl',None))
                    self.assertEqual(after,before)

    def test_cached_empty_and_unparseable_maps_follow_new_language_without_file_changes(self):
        empty=self.root/'empty';empty.mkdir();broken=self.root/'broken';broken.mkdir();(broken/'bad.py').write_bytes(b'def broken(\n');before=self.snapshot()
        for language in ('en','fr','zh','ru','fr','en'):
            with fl.use(language):
                self.assertEqual(workshop._map_for_root(empty),fl.text('workshop.project.115'))
                self.assertEqual(workshop._map_for_root(broken),fl.text('workshop.project.114',v0='bad.py'));self.assertEqual(self.snapshot(),before)

    def test_send_refusal_localizes_only_owned_words_without_admitting_a_send(self):
        for language in fl.LANGUAGES:
            with fl.use(language),patch.object(workshop,'_resolve_read',return_value=self.root/'absent'):
                self.assertEqual(workshop.send_file('workspace/'+RAW),fl.text('workshop.project.109',v1='workspace/'+RAW))
        self.assertFalse(list(self.root.rglob('*')))

if __name__=='__main__':unittest.main()
