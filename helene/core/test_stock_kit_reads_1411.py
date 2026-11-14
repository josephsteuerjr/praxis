"""Every packaged material address advertised in K must be readable by fs_read."""
import os
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import frame_language
import resident_skills
import workshop


class KitReads(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.kit=self.root/'app/resources';self.home=self.root/'data'
        self.skills=self.home/'soul/skills';self.skills.mkdir(parents=True)
        env=patch.dict(os.environ,{'HELENE_SKILLS_KIT_ROOT':str(self.kit)});env.start();self.addCleanup(env.stop)
        base=patch.object(workshop,'BASE',self.home);base.start();self.addCleanup(base.stop)
        repo=patch.object(workshop,'REPO',self.root/'tree');repo.start();self.addCleanup(repo.stop)
        for lang in frame_language.LANGUAGES:
            folder=self.kit/('soul' if lang=='ru' else 'soul.'+lang)/'skills/living-prose'
            folder.mkdir(parents=True)
            (folder/'resident.md').write_text('RESIDENT_'+lang,encoding='utf-8')
            (folder/'practice.md').write_text('MATERIAL_'+lang,encoding='utf-8')
            folder.with_suffix('.md').write_text('SKILL_'+lang,encoding='utf-8')

    def test_rendered_addresses_resolve_and_return_actual_file_bytes(self):
        for lang in frame_language.LANGUAGES:
            with frame_language.use(lang):
                rows=resident_skills.entries(self.skills)
                row=rows[0]
                self.assertIn('RESIDENT_'+lang,workshop.fs_read(row['path']))
                self.assertIn('SKILL_'+lang,workshop.fs_read(row['skill_path']))
                for name in row['materials']:
                    address=row['path'].rsplit('/',1)[0]+'/'+name
                    self.assertIn('MATERIAL_'+lang,workshop.fs_read(address))
                    self.assertIn('whole-file sha256=',workshop.fs_read(address))
        self.assertEqual(list(self.skills.iterdir()),[])

    def test_kit_cannot_be_written_or_escape_into_other_resources(self):
        for address in ('kit://soul.en/skills/living-prose/practice.md','kit://soul/VOICE.md'):
            self.assertIsNone(workshop._resolve_write(address)[0])
            self.assertIsNone(workshop._resolve_write(address,proposal_id='some-proposal')[0])
        for address in ('kit://../secret','kit:///soul/VOICE.md','kit://soul/../../secret',
                        'kit://soul\\..\\..\\secret','kit://components/token','kit://C:/secret',
                        'kit://soul.en/skills/name:stream'):
            self.assertIsNone(workshop._resolve_read(address),address)
        with patch.dict(os.environ,{'HELENE_SKILLS_KIT_ROOT':''}):
            self.assertIsNone(workshop._resolve_read('kit://soul/VOICE.md'))

    def test_link_cannot_export_a_file_outside_stock(self):
        private=self.root/'private';private.mkdir();(private/'secret.md').write_text('PRIVATE',encoding='utf-8')
        link=self.kit/'soul.en/outside'
        try:link.symlink_to(private,target_is_directory=True)
        except OSError:self.skipTest('symlink creation unavailable on this host')
        self.assertIsNone(workshop._resolve_read('kit://soul.en/outside/secret.md'))

    def test_actual_agent_read_and_hash_paging_keep_the_kit_address(self):
        import agent
        address='kit://soul.en/skills/living-prose/practice.md'
        raw=('A'*12000+'\nSECOND LINE\n').encode('utf-8')
        target=self.kit/'soul.en/skills/living-prose/practice.md';target.write_bytes(raw)
        digest=hashlib.sha256(raw).hexdigest()
        self.assertIn(digest,str(agent.tool_fs_read(path=address)))
        first=json.loads(str(agent.tool_fs_read(path=address,expected_sha256=digest)))
        self.assertEqual(first['path'],address)
        self.assertLessEqual(len(json.dumps(first,ensure_ascii=False)),8000)
        second=json.loads(str(agent.tool_fs_read(path=address,expected_sha256=digest,
                         start=first['next_start'],start_char=first['next_char'])))
        self.assertEqual(second['path'],address)
        self.assertEqual((first['text']+second['text']).encode('utf-8'),raw)
        self.assertEqual(second['sha256'],digest)
        tools=agent.BASE_TOOLS+agent.OWNER_TOOLS+agent.PRAXIS_SELF_TOOLS+agent.SHARED_CONTEXT_TOOLS
        self.assertIn('kit://',next(t['description'] for t in tools if t['name']=='fs_read'))

    def test_skill_navigation_names_real_packaged_files_in_all_frame_languages(self):
        import capabilities
        for lang in frame_language.LANGUAGES:
            with frame_language.use(lang):
                row=capabilities._skills_list(self.skills/'INDEX.md')[0]
                self.assertEqual(row['source'],'kit')
                self.assertFalse(row.get('orphan'))
                self.assertIsNotNone(workshop._resolve_read(row['path']))
                navigation=capabilities.skill_navigation(self.skills/'INDEX.md')
                self.assertIn(row['path'],navigation)
                resident=capabilities._resident_line([row])
                self.assertIn('kit://',resident)
                if lang!='ru':
                    self.assertNotRegex(navigation+resident,r'[А-Яа-яЁё]')
                for path in row['materials']:
                    self.assertIn('MATERIAL_'+lang,workshop.fs_read(path))


if __name__=='__main__':unittest.main()
