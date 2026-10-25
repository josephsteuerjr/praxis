"""Causal package/seed/code transition checks; disposable Linux homes only."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

DESK=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(DESK),str(DESK/'installer')]
from localharness import code_transition as code, python_source_updater as updater, python_updates as updates
from localharness import package_bootstrap as bootstrap
from python_posix_packages import deb_control

def put(root,relative,data):
    p=root/relative;p.parent.mkdir(parents=True,exist_ok=True)
    p.write_bytes(data if isinstance(data,bytes) else data.encode());return p

def sha(data):return hashlib.sha256(data.encode()).hexdigest()

class SeedTransition(unittest.TestCase):
    def test_update_extraction_reuses_only_verified_runtime_members_and_retains_current_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);old=root/'program';out=root/'unpacked';archive=root/'update.zip'
            current=put(old,'runtime/library','same')
            put(old,'helene.json','owner')
            with zipfile.ZipFile(archive,'w') as z:
                member=zipfile.ZipInfo('Helene/runtime/library');member.external_attr=(stat.S_IFREG|stat.S_IMODE(current.stat().st_mode))<<16
                z.writestr(member,'same')
                z.writestr('Helene/helene.json','incoming')
                z.writestr('Helene/python-package-files.json',json.dumps({'files':{'runtime/library':sha('same'),'helene.json':sha('incoming')}}))
            updates._extract(archive,out,reuse_from=old)
            self.assertEqual(current.stat().st_ino,(out/'Helene/runtime/library').stat().st_ino)
            self.assertNotEqual((old/'helene.json').stat().st_ino,(out/'Helene/helene.json').stat().st_ino)
            self.assertEqual((old/'helene.json').read_text(),'owner')

    def test_inventory_cannot_mask_different_zip_runtime_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);old=root/'program';out=root/'unpacked';archive=root/'update.zip'
            current=put(old,'runtime/library','same')
            with zipfile.ZipFile(archive,'w') as z:
                member=zipfile.ZipInfo('Helene/runtime/library');member.external_attr=(stat.S_IFREG|stat.S_IMODE(current.stat().st_mode))<<16
                z.writestr(member,'changed')
                z.writestr('Helene/python-package-files.json',json.dumps({'files':{'runtime/library':sha('same')}}))
            updates._extract(archive,out,reuse_from=old)
            self.assertEqual(current.read_text(),'same')
            self.assertEqual((out/'Helene/runtime/library').read_text(),'changed')
            self.assertNotEqual(current.stat().st_ino,(out/'Helene/runtime/library').stat().st_ino)

    def test_same_version_repacked_seed_is_explicit_but_newer_user_update_wins(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);seed=root/'seed';program=root/'program'
            for p in (seed,program):
                put(p,'python-app.json','{"version":"2.0.0"}');put(p,'python-package-files.json','{"files":{}}')
            self.assertFalse(bootstrap.transition(seed,program)['needed'])
            put(seed,'python-package-files.json','{"files":{"app/entry":"new"}}')
            self.assertTrue(bootstrap.transition(seed,program)['needed'])
            put(program,'python-app.json','{"version":"2.1.0"}')
            self.assertFalse(bootstrap.transition(seed,program)['needed'])

    def test_verified_user_runtime_reuse_does_not_link_mutable_profile_or_seed_inode(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);seed=root/'seed';program=root/'program';stage=root/'stage'
            for p in (seed,program):
                put(p,'runtime/immutable','same');put(p,'helene.json','{"authored":"keep"}')
            put(seed,'python-package-files.json',json.dumps({'files':{'runtime/immutable':sha('same')}}))
            bootstrap.clone_seed(seed,stage,program)
            self.assertEqual((stage/'runtime/immutable').stat().st_ino,(program/'runtime/immutable').stat().st_ino)
            self.assertNotEqual((stage/'runtime/immutable').stat().st_ino,(seed/'runtime/immutable').stat().st_ino)
            self.assertNotEqual((stage/'helene.json').stat().st_ino,(program/'helene.json').stat().st_ino)
            put(seed,'runtime/immutable','new')
            self.assertEqual((program/'runtime/immutable').read_text(),'same')

class SourceTransition(unittest.TestCase):
    def test_baseline_uses_shipped_derived_files_never_historical_donor_hashes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            put(root,'selected-source.json',json.dumps({'snapshot_files':{'agent.py':{'sha256':sha('donor')}},'files':{'agent.py':{'sha256':sha('derived shipped code')}}}))
            self.assertEqual(code.baseline(root)['agent.py'],sha('derived shipped code'))

    def fixture(self,base):
        old=base/'old';new=base/'incoming'
        put(old,'helene.json','{"tree":"data","code":"tree","runner":""}')
        put(old,'data/memory/history','private history');put(old,'data/soul/identity.md','private identity')
        for n,data in {'stock.py':'STOCK=1','authored.py':'AUTHORED=0','conflict.py':'VALUE=1'}.items():put(old,'tree/'+n,data)
        put(old,'shipped-code.json',json.dumps({'files':{n:sha(data) for n,data in {'stock.py':'STOCK=1','authored.py':'AUTHORED=0','conflict.py':'VALUE=1'}.items()}}))
        def git(*args):return subprocess.check_output(['git','-C',str(old/'tree'),*args],stderr=subprocess.STDOUT)
        git('init');git('add','.');git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','stock')
        put(old,'tree/authored.py','AUTHORED=1');git('add','authored.py')
        put(old,'tree/authored.py','AUTHORED=2');put(old,'tree/untracked.py','USER=1')
        for n,data in {'stock.py':'STOCK=2','authored.py':'AUTHORED=0','conflict.py':'VALUE=1','new.py':'NEW=1'}.items():put(new,'tree/'+n,data)
        put(new,'python-app.json','{"target":"linux-x86_64"}')
        return old,new,git

    def test_product_code_updates_and_git_authored_memory_config_remain_independent(self):
        with tempfile.TemporaryDirectory() as d:
            old,new,git=self.fixture(Path(d));head=git('rev-parse','HEAD');index=(old/'tree/.git/index').read_bytes()
            note,recovery=updater.install(old,new)
            value=json.loads((old/'helene.json').read_text());selected=Path(value['code'])
            self.assertEqual((selected/'stock.py').read_text(),'STOCK=2')
            self.assertEqual((selected/'authored.py').read_text(),'AUTHORED=2')
            self.assertEqual((selected/'untracked.py').read_text(),'USER=1')
            self.assertEqual((selected/'new.py').read_text(),'NEW=1')
            self.assertEqual((selected/'.git/index').read_bytes(),index)
            self.assertEqual(subprocess.check_output(['git','-C',str(selected),'rev-parse','HEAD']),head)
            self.assertEqual((Path(value['tree'])/'memory/history').read_text(),'private history')
            self.assertEqual((Path(value['tree'])/'soul/identity.md').read_text(),'private identity')
            self.assertEqual((old/'tree/stock.py').read_text(),'STOCK=2') # packaged stock also advances
            updater.rollback(old,recovery)
            self.assertEqual((old/'tree/stock.py').read_text(),'STOCK=1')
            self.assertEqual(json.loads((old/'helene.json').read_text())['code'],'tree')

    def test_overlapping_authored_change_refuses_without_overlay_and_names_both_candidates(self):
        with tempfile.TemporaryDirectory() as d:
            old,new,git=self.fixture(Path(d))
            put(old,'tree/conflict.py','OWNER=2');put(new,'tree/conflict.py','INCOMING=3')
            before={p.relative_to(old):p.read_bytes() for p in old.rglob('*') if p.is_file()}
            with self.assertRaisesRegex(RuntimeError,'code conflicts'):updater.install(old,new)
            report=json.loads((new/'code-transition.json').read_text())
            row=next(r for r in report['conflicts'] if r['path']=='conflict.py')
            self.assertEqual(Path(row['owner_file']).read_text(),'OWNER=2')
            self.assertEqual(Path(row['incoming_file']).read_text(),'INCOMING=3')
            for rel,data in before.items():self.assertEqual((old/rel).read_bytes(),data,rel)
            self.assertFalse(updater.active_update(old).exists())

class DebianControl(unittest.TestCase):
    def test_transition_is_bounded_to_legacy_name(self):
        control=deb_control('2.0.0','2.36')
        self.assertIn('Conflicts: helene (<< 2.0.0)',control)
        self.assertIn('Replaces: helene (<< 2.0.0)',control)

if __name__=='__main__':unittest.main(verbosity=2)
