"""Real explicit Create, one-use layout authority, interruption and no rebirth."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import builtins,json,os,socket,sys,threading,unittest
from unittest.mock import patch
from localharness import boot,first_birth,first_start as common,posix_first_start as first
from tests import t_posix_first_start_0111 as fixture


class FirstBirth(unittest.TestCase):
    def setUp(self):
        fixture.FirstStart.setUp(self)
        self.factory={p.relative_to(self.root).as_posix():p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        first.prepare(self.root,places=self.places)
        self.values=dict(name='Birth fixture',owner='Owner',provider='openai',model='fixture',key='fixture-only')
        self.text='# {{agent}}\nOwner: {{owner}}\nUnicode: Привет\n<script>plain text</script>\n'
        self.soul=self.data/'soul/SOUL.md';self.seed=self.data/'memory/.state/soul-seed.md'
        self.authority=self.root/'kit-birth.json';self.identity=self.data/'memory/.control/profile-identity.json'
        self.before=(self.root/'helene.json').read_bytes()
        # Git/current-self construction has separate tests; these failures are
        # honestly PARTIAL under the existing layout protocol, never complete.
        for method,result in (('seed_git',False),('_import_self_model',None)):
            p=patch.object(boot,method,return_value=result);p.start();self.addCleanup(p.stop)
        self.addCleanup(boot.release_tree)

    def create(self,text=None,kind='text'):
        choice={'kind':kind}
        if kind=='text':choice['text']=self.text if text is None else text
        return first.create_agent(self.root,{**self.values,'soul':choice},places=self.places)

    def doc(self):return json.loads(self.authority.read_text(encoding='utf8'))

    def fail_before_layout(self):
        with patch.object(boot,'ensure_layout',side_effect=OSError('pre-layout fixture')):
            with self.assertRaises(OSError):self.create()

    def interrupt_after_soul(self):
        with patch.object(boot,'_seed_kit',side_effect=OSError('interrupted kit fixture')):
            with self.assertRaises(OSError):self.create()

    def test_custom_create_materializes_real_SOUL_and_consumes_real_authority_before_login(self):
        self.assertTrue(self.create()['created'])
        self.assertEqual(self.soul.read_text(encoding='utf8'),boot._names(self.text,common.load(self.root/'helene.json')))
        row=self.doc();self.assertEqual(row['source'],first_birth.SOURCE)
        self.assertEqual(row['tree'],str(self.data.resolve()));self.assertEqual(row['state'],'partial')
        self.assertTrue(row['creation']['published']);self.assertTrue(self.authority.with_name(self.authority.name+'.claim').is_file())
        self.assertEqual(self.seed.read_bytes(),self.text.encode('utf8'))

    def test_explicit_canonical_creates_the_actual_canonical_SOUL(self):
        self.create(kind='canonical')
        self.assertEqual(self.soul.read_text(encoding='utf8'),boot.soul_text(common.load(self.root/'helene.json')))

    def test_BOM_choice_uses_the_same_decoded_seed_as_the_runner(self):
        self.create('\ufeff# BOM choice\n')
        self.assertEqual(self.seed.read_bytes(),b'\xef\xbb\xbf# BOM choice\n')
        self.assertEqual(self.soul.read_bytes(),b'# BOM choice\n')

    def test_legacy_caller_without_soul_gets_a_real_birth_without_new_profile_seed(self):
        first.create_agent(self.root,self.values,places=self.places)
        self.assertFalse(self.seed.exists())
        self.assertEqual(self.soul.read_text(encoding='utf8'),boot.soul_text(common.load(self.root/'helene.json')))

    def test_new_programme_before_subscription_login_keeps_selected_and_authored_SOUL(self):
        self.values.update(provider='chatgpt',key='')
        with patch('localharness.standalone_relay.ensure_configured',return_value=None):self.create()
        chosen=self.seed.read_bytes();authored=self.soul.read_bytes()+b'Owner revision before sign-in\n';self.soul.write_bytes(authored)
        incoming=self.base/'New programme';incoming.mkdir()
        for name,raw in self.factory.items():
            p=incoming/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        first.prepare(incoming,places=self.places)
        self.assertFalse((incoming/'kit-birth.json').exists())
        config=incoming/'helene.json'
        boot.ensure_layout(self.data,common.load(config),soul_seed=boot.initial_soul_seed(config,self.data),birth_receipt=config.with_name('kit-birth.json'))
        self.assertEqual(self.soul.read_bytes(),authored);self.assertEqual(self.seed.read_bytes(),chosen)

    def test_repeated_Create_preserves_constitution_seed_identity_and_receipt(self):
        self.create();saved=[p.read_bytes() for p in (self.soul,self.seed,self.identity,self.authority)]
        with self.assertRaises(ValueError):self.create('different')
        self.assertEqual([p.read_bytes() for p in (self.soul,self.seed,self.identity,self.authority)],saved)

    def test_two_concurrent_Create_requests_have_only_one_committed_birth(self):
        barrier=threading.Barrier(2)
        def call(_):
            barrier.wait(timeout=10)
            try:return bool(self.create().get('created'))
            except (ValueError,OSError,boot.LayoutError):return False
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(call,range(2)))
        self.assertEqual(sorted(results),[False,True]);self.assertTrue(self.identity.is_file())
        self.assertTrue(self.doc()['creation']['published'])

    def test_failed_relay_does_not_publish_seed_identity_or_birth_and_can_retry(self):
        self.values.update(provider='chatgpt',key='')
        with patch('localharness.standalone_relay.ensure_configured',side_effect=OSError('offline fixture')):
            with self.assertRaises(OSError):self.create()
        for p in (self.seed,self.identity,self.authority,self.soul):self.assertFalse(p.exists(),str(p))
        self.assertEqual((self.root/'helene.json').read_bytes(),self.before)
        self.values.update(provider='openai',key='fixture-only');self.assertTrue(self.create()['created'])

    def test_pre_layout_failure_can_retry_the_same_unclaimed_authority(self):
        self.fail_before_layout();first_id=self.doc()['creation']['id']
        self.assertEqual(self.doc()['state'],'prepared');self.assertFalse(self.soul.exists());self.assertFalse(self.identity.exists())
        self.assertTrue(self.create()['created']);self.assertEqual(self.doc()['creation']['id'],first_id)

    def test_interrupted_kit_with_retained_SOUL_recovers_as_partial_without_another_birth(self):
        self.interrupt_after_soul();before=self.soul.read_bytes();claim=self.authority.with_name(self.authority.name+'.claim')
        retained=claim.read_bytes();first_id=self.doc()['creation']['id'];self.assertEqual(self.doc()['state'],'started')
        self.assertTrue(self.create()['created']);self.assertEqual(self.doc()['state'],'partial')
        self.assertEqual(self.doc()['creation']['id'],first_id);self.assertEqual(claim.read_bytes(),retained)
        self.assertEqual(self.soul.read_bytes(),before)

    def test_started_without_SOUL_never_reprepares_ambiguous_missing_data(self):
        write=Path.write_text
        def refuse(path,*args,**kwargs):
            if path==self.soul:raise OSError('SOUL write fixture')
            return write(path,*args,**kwargs)
        with patch.object(Path,'write_text',refuse):
            with self.assertRaises(boot.LayoutError):self.create()
        self.assertEqual(self.doc()['state'],'started');before=self.authority.read_bytes()
        with self.assertRaises(ValueError):self.create()
        self.assertEqual(self.authority.read_bytes(),before);self.assertFalse(self.soul.exists())

    def test_lost_data_after_completed_creation_is_not_permission_to_rebirth(self):
        self.create();saved=self.soul.read_bytes();authority=self.authority.read_bytes();rescue=self.base/'Rescued profile'
        self.assertTrue(self.data.resolve().is_relative_to(self.base.resolve()));self.assertTrue(rescue.resolve().is_relative_to(self.base.resolve()))
        self.data.rename(rescue);self.data.mkdir()
        with self.assertRaises(ValueError):self.create()
        self.assertFalse(self.soul.exists());self.assertEqual((rescue/'soul/SOUL.md').read_bytes(),saved)
        self.assertEqual(self.authority.read_bytes(),authority)

    def test_changed_pending_choice_is_refused_before_rewriting_its_seed(self):
        self.fail_before_layout();before=self.seed.read_bytes();authority=self.authority.read_bytes()
        with self.assertRaises(ValueError):self.create('replacement choice')
        self.assertEqual(self.seed.read_bytes(),before);self.assertEqual(self.authority.read_bytes(),authority)

    def test_authored_SOUL_after_interruption_is_preserved_and_not_adopted_as_a_new_birth(self):
        self.interrupt_after_soul();authored=b'Owner authored after interruption\n';self.soul.write_bytes(authored)
        with self.assertRaises(ValueError):self.create()
        self.assertEqual(self.soul.read_bytes(),authored);self.assertFalse(self.identity.exists())

    def test_foreign_prepared_authority_is_not_overwritten_by_Create(self):
        raw=json.dumps({'schema':1,'state':'prepared','source':'foreign setup','tree':str(self.data.resolve())}).encode()
        self.authority.write_bytes(raw)
        with self.assertRaises(ValueError):self.create()
        self.assertEqual(self.authority.read_bytes(),raw);self.assertFalse(self.soul.exists())

    def test_existing_tree_owner_is_never_displaced(self):
        self.assertEqual(boot.claim_tree(self.data,role='other owner'),{})
        path=boot.tree_lock_path(self.data);before=path.read_bytes()
        with self.assertRaises(boot.LayoutError):self.create()
        self.assertEqual(path.read_bytes(),before);self.assertFalse(self.authority.exists())

    def test_legacy_fail_open_lock_result_is_insufficient_for_birth(self):
        with patch.object(boot,'claim_tree',return_value={}):
            with self.assertRaises(boot.LayoutError):self.create()
        for p in (self.soul,self.seed,self.authority,self.identity):self.assertFalse(p.exists(),str(p))

    def test_an_existing_unreadable_lock_is_not_deleted_as_if_it_were_dead(self):
        path=boot.tree_lock_path(self.data);path.parent.mkdir(parents=True,exist_ok=True);raw=b'unknown owner generation'
        path.write_bytes(raw)
        with self.assertRaises(boot.LayoutError):self.create()
        self.assertEqual(path.read_bytes(),raw);self.assertFalse(self.authority.exists())

    def test_explicit_creation_lease_keeps_the_live_process_generation_beyond_the_old_TTL(self):
        with boot.birth_custody(self.data):
            holder=boot._read_lock(boot.tree_lock_path(self.data));holder['at']=0
            self.assertTrue(boot._lock_is_live(holder))
            unknown=dict(holder,birth=None);self.assertTrue(boot._lock_is_live(unknown))
            nonfinite=dict(holder,birth=float('nan'));self.assertTrue(boot._lock_is_live(nonfinite))
            remote=dict(holder,host='synthetic-other-host');self.assertTrue(boot._lock_is_live(remote))

    def test_a_racing_unknown_reservation_is_not_unlinked_during_strict_birth_claim(self):
        claim=boot.claim_tree;path=boot.tree_lock_path(self.data);raw=b'new owner is still writing its receipt'
        def reserve(tree,**kwargs):
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            return claim(tree,**kwargs)
        with patch.object(boot,'claim_tree',side_effect=reserve):
            with self.assertRaises(boot.LayoutError):self.create()
        self.assertEqual(path.read_bytes(),raw);self.assertFalse(self.soul.exists())

    def test_a_normal_runner_also_preserves_an_unreadable_owner_reservation(self):
        path=boot.tree_lock_path(self.data);path.parent.mkdir(parents=True,exist_ok=True);raw=b'unknown owner'
        path.write_bytes(raw);self.assertTrue(boot.claim_tree(self.data))
        self.assertEqual(path.read_bytes(),raw)

    def test_malformed_pending_authority_is_refused_without_rewriting_choice(self):
        self.fail_before_layout();original=self.doc();seed=self.seed.read_bytes()
        variants=[{**original,'schema':True},{**original,'gaps':'not-a-list'},
            {**original,'creation':{**original['creation'],'id':'not-a-valid-id'}},
            {**original,'creation':{**original['creation'],'soul_sha256':{}}}]
        for value in variants:
            with self.subTest(value=value):
                self.authority.write_text(json.dumps(value),encoding='utf8');before=self.authority.read_bytes()
                with self.assertRaises(ValueError):self.create()
                self.assertEqual(self.authority.read_bytes(),before);self.assertEqual(self.seed.read_bytes(),seed)
        self.assertFalse(self.soul.exists())

    def test_identity_publication_failure_can_finish_without_replacing_SOUL(self):
        write=common._write
        def refuse(path,*args,**kwargs):
            if Path(path)==self.identity:raise OSError('identity publication fixture')
            return write(path,*args,**kwargs)
        with patch.object(common,'_write',side_effect=refuse):
            with self.assertRaises(OSError):self.create()
        before=self.soul.read_bytes();first_id=self.doc()['creation']['id'];self.assertFalse(self.identity.exists())
        self.assertTrue(self.create()['created']);self.assertEqual(self.soul.read_bytes(),before)
        self.assertEqual(self.doc()['creation']['id'],first_id)

    def test_creation_never_imports_the_agent_or_connects_to_a_model(self):
        imported=builtins.__import__;before=list(sys.path)
        def guard(name,*args,**kwargs):
            if name in ('agent','mtproto_runner'):raise AssertionError('Creation must not start an agent')
            return imported(name,*args,**kwargs)
        with patch('builtins.__import__',side_effect=guard),patch.object(socket.socket,'connect',side_effect=AssertionError('No model connection')):
            self.create()
        self.assertEqual(sys.path,before)


if __name__=='__main__':unittest.main(verbosity=2)
