"""Owner birth choice survives first-login and programme-folder boundaries."""
import json,os,unittest
from pathlib import Path
from unittest.mock import patch
from localharness import boot,first_start as common,posix_first_start as first
from tests import t_posix_first_start_0111 as profile_fixture


class Constitution(unittest.TestCase):
    def setUp(self):
        profile_fixture.FirstStart.setUp(self)
        first.prepare(self.root,places=self.places)
        self.values=dict(name='Fixture',owner='Owner',provider='openai',model='fixture',key='fixture-only')
        self.seed=self.data/'memory/.state/soul-seed.md'

    def create(self,choice):
        first.create_agent(self.root,{**self.values,'soul':choice},places=self.places)

    def test_custom_choice_is_exact_profile_data_and_survives_programme_move(self):
        text='# Моя конституция\n\nOwner-authored <script>plain text</script>\n'
        self.create({'kind':'text','text':text})
        self.assertEqual(self.seed.read_bytes(),text.encode('utf-8'))
        if os.name!='nt':self.assertEqual(self.seed.stat().st_mode&0o777,0o600)
        moved=self.root.with_name('Moved programme');self.root.rename(moved)
        self.assertEqual(boot.initial_soul_seed(moved/'helene.json',self.data),text)
        self.assertFalse((moved/'soul-seed.md').exists())

    def test_explicit_canonical_choice_overrides_a_stale_programme_seed(self):
        (self.root/'soul-seed.md').write_text('old unrelated programme seed',encoding='utf-8')
        self.create({'kind':'canonical'})
        chosen=boot.soul_text(common.load(self.root/'helene.json'))
        self.assertEqual(boot.initial_soul_seed(self.root/'helene.json',self.data),chosen)
        self.assertNotEqual(chosen,'old unrelated programme seed')

    def test_invalid_choice_is_rejected_before_identity_or_credentials_are_published(self):
        before=(self.root/'helene.json').read_bytes()
        for choice in (None,[],{'kind':'inherit'},{'kind':'text','text':' \n'},{'kind':'text','text':[]}):
            with self.subTest(choice=choice),self.assertRaises(ValueError):self.create(choice)
        self.assertFalse(self.seed.exists());self.assertFalse(first.agent_exists(self.data))
        self.assertEqual((self.root/'helene.json').read_bytes(),before)

    def test_failed_subscription_install_keeps_choice_and_identity_unpublished(self):
        self.values.update(provider='chatgpt',key='')
        with patch('localharness.standalone_relay.ensure_configured',side_effect=OSError('offline fixture')):
            with self.assertRaises(OSError):self.create({'kind':'text','text':'chosen but not admitted'})
        self.assertFalse(self.seed.exists());self.assertFalse(first.agent_exists(self.data))

    def test_an_existing_agent_cannot_have_its_constitution_replaced_by_create(self):
        self.create({'kind':'text','text':'original birth choice'})
        before=self.seed.read_bytes()
        with self.assertRaises(ValueError):self.create({'kind':'text','text':'replacement'})
        self.assertEqual(self.seed.read_bytes(),before)

    def test_unreadable_explicit_profile_choice_never_silently_becomes_canonical(self):
        self.seed.parent.mkdir(parents=True,exist_ok=True);self.seed.mkdir()
        with self.assertRaises(boot.LayoutError):boot.initial_soul_seed(self.root/'helene.json',self.data)

    def test_secondary_agent_legacy_seed_and_missing_seed_keep_their_existing_contract(self):
        self.assertIsNone(boot.initial_soul_seed(self.root/'helene.json',self.data))
        legacy=self.root/'soul-seed.md';legacy.write_bytes(b'\xef\xbb\xbflegacy birth\n')
        self.assertEqual(boot.initial_soul_seed(self.root/'helene.json',self.data),'legacy birth\n')

    def test_a_damaged_birth_record_never_blocks_an_existing_live_constitution(self):
        soul=self.data/'soul/SOUL.md';soul.parent.mkdir();soul.write_text('Living owner-authored constitution',encoding='utf-8')
        self.seed.parent.mkdir(parents=True,exist_ok=True);self.seed.mkdir()
        self.assertIsNone(boot.initial_soul_seed(self.root/'helene.json',self.data))
        self.assertEqual(soul.read_text(encoding='utf-8'),'Living owner-authored constitution')


if __name__=='__main__':unittest.main(verbosity=2)
