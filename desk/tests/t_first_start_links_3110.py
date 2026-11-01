"""Real owner-data file aliases, without model-specific directory rules."""
from pathlib import Path
import hashlib,os,stat,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import first_start as start,first_start_assets as assets


class DataLinks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='data-alias-');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.source=self.base/'old';self.source.mkdir()
        self.target=self.base/'new'

    def cached(self,absolute=False):
        blob=self.source/'objects/payload';blob.parent.mkdir();blob.write_bytes(b'owner model bytes')
        alias=self.source/'snapshots/revision/value.json';alias.parent.mkdir(parents=True)
        alias.symlink_to(blob if absolute else Path('../../objects/payload'))
        return blob,alias

    def test_merge_internal_file_alias_without_copying_blob_twice(self):
        blob,alias=self.cached();relative=alias.relative_to(self.source)
        self.target.mkdir();(self.target/'keep').write_bytes(b'owner primary data')
        start._move(self.source,self.target)
        actual=self.target/relative
        self.assertEqual(actual.read_bytes(),b'owner model bytes')
        self.assertFalse(actual.is_symlink())
        self.assertEqual(actual.stat().st_ino,(self.target/'objects/payload').stat().st_ino)
        self.assertEqual((self.target/'keep').read_bytes(),b'owner primary data')
        self.assertFalse(self.source.exists())

    def test_absolute_internal_alias_survives_whole_directory_move(self):
        _,alias=self.cached(absolute=True);relative=alias.relative_to(self.source)
        start._move(self.source,self.target)
        self.assertEqual((self.target/relative).read_bytes(),b'owner model bytes')
        self.assertFalse((self.target/relative).is_symlink())

    def test_snapshot_measures_internal_alias_as_logical_file_bytes(self):
        blob,alias=self.cached()
        value=assets.snapshot(self.source)
        expected=hashlib.sha256(blob.read_bytes()).hexdigest()
        self.assertEqual(value['files'][alias.relative_to(self.source).as_posix()],expected)
        self.assertEqual(value['files'][blob.relative_to(self.source).as_posix()],expected)

    def test_conflicting_primary_blob_does_not_rebind_incoming_alias(self):
        _,alias=self.cached();relative=alias.relative_to(self.source)
        existing=self.target/'objects/payload';existing.parent.mkdir(parents=True);existing.write_bytes(b'primary model')
        start._move(self.source,self.target)
        self.assertEqual(existing.read_bytes(),b'primary model')
        self.assertEqual((self.target/relative).read_bytes(),b'owner model bytes')
        recovered=list((self.target/'recovered').glob('*/objects/payload'))
        self.assertEqual(len(recovered),1)
        self.assertEqual((self.target/relative).stat().st_ino,recovered[0].stat().st_ino)

    def test_external_file_alias_is_rejected_before_any_data_move(self):
        outside=self.base/'outside';outside.write_bytes(b'outside owner data')
        normal=self.source/'keep';normal.write_bytes(b'keep source')
        alias=self.source/'alias';alias.symlink_to(outside)
        self.target.mkdir()
        with self.assertRaises(ValueError):start._move(self.source,self.target)
        self.assertEqual(normal.read_bytes(),b'keep source')
        self.assertEqual(outside.read_bytes(),b'outside owner data')
        self.assertTrue(alias.is_symlink())
        self.assertEqual(list(self.target.iterdir()),[])

    def test_directory_alias_is_not_traversed_or_moved(self):
        outside=self.base/'outside';outside.mkdir();(outside/'keep').write_bytes(b'external')
        (self.source/'directory').symlink_to(outside,target_is_directory=True)
        self.target.mkdir()
        with self.assertRaises(ValueError):start._move(self.source,self.target)
        self.assertEqual((outside/'keep').read_bytes(),b'external')
        self.assertTrue((self.source/'directory').is_symlink())

    def test_broken_file_alias_keeps_original_tree_intact(self):
        (self.source/'keep').write_bytes(b'keep source')
        (self.source/'alias').symlink_to('missing')
        self.target.mkdir()
        with self.assertRaises(ValueError):start._move(self.source,self.target)
        self.assertEqual((self.source/'keep').read_bytes(),b'keep source')

    def test_retry_after_partial_move_keeps_the_remaining_alias_readable(self):
        _,alias=self.cached();relative=alias.relative_to(self.source);self.target.mkdir()
        real_link=os.link
        def interrupted(incoming,out,*args,**kwargs):
            if Path(out).is_relative_to(self.target):raise OSError('injected transfer interruption')
            return real_link(incoming,out,*args,**kwargs)
        with patch.object(os,'link',side_effect=interrupted),self.assertRaises(OSError):
            start._move(self.source,self.target)
        self.assertEqual(alias.read_bytes(),b'owner model bytes')
        self.assertFalse(alias.is_symlink())
        start._move(self.source,self.target)
        self.assertEqual((self.target/relative).read_bytes(),b'owner model bytes')
        self.assertFalse(self.source.exists())

    def test_known_folder_locations_need_not_be_children_of_the_profile(self):
        from deskd import local_files
        profile=self.base/'profile';downloads=self.base/'relocated/downloads';local=self.base/'relocated/local'
        values={'5E6C858F-0E22-4760-9AFE-EA3317B67173':profile,
                '374DE290-123F-4565-9164-39C4925E467B':downloads,
                'F1B32785-6FBA-4FCF-9D55-7B8E7F157091':local,
                '3EB685DB-65F9-4CF6-A03A-E3EF65729F3D':self.base/'relocated/roaming'}
        with patch.object(local_files,'_known_folder',side_effect=values.get):places=start.Places.current()
        self.assertEqual(places.profile,profile);self.assertEqual(places.downloads,downloads)
        self.assertEqual(places.appdata[0],local)
        self.assertEqual(places.data,profile/'Helene/data')

    def test_readonly_shared_blob_keeps_its_attributes_and_one_transferred_copy(self):
        blob,alias=self.cached();relative=alias.relative_to(self.source)
        outside=self.base/'shared-owner-file';os.link(blob,outside)
        mode=blob.stat().st_mode;blob.chmod(stat.S_IREAD);self.target.mkdir()
        try:
            start._move(self.source,self.target)
            actual=self.target/relative;stored=self.target/'objects/payload'
            self.assertEqual(actual.read_bytes(),b'owner model bytes')
            self.assertEqual(actual.stat().st_ino,stored.stat().st_ino)
            self.assertFalse(outside.stat().st_mode&stat.S_IWRITE)
            self.assertFalse(stored.stat().st_mode&stat.S_IWRITE)
            self.assertEqual(outside.read_bytes(),b'owner model bytes')
        finally:outside.chmod(mode)


if __name__=='__main__':unittest.main()
