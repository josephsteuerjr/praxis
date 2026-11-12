"""Inventory failure must precede replacement of any destination data."""
import io,json,stat,sys,tempfile,unittest,zipfile
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'localharness')]
import carry
from localharness import carry_inventory as inventory


class Inventory(unittest.TestCase):
    def archive(self,names):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            for name,data in names:
                info=zipfile.ZipInfo(name);info.filename=name;info.orig_filename=name
                archive.writestr(info,data)
        stream.seek(0)
        return zipfile.ZipFile(stream)

    def test_windows_aliases_and_file_parent_conflicts_refused_before_extract(self):
        for names in ([('data/A',b'a'),('data/a',b'b')],[('data/file',b'a'),('data/file/child',b'b')],
                      [('data/a.',b'a')],[('data/NUL.txt',b'a')],[('data/file:stream',b'a')],
                      [('data/../outside',b'a')],[('data\\outside',b'a')]):
            with self.subTest(names=names),self.archive(names) as archive:
                with self.assertRaises(ValueError):inventory.verify(archive,windows=True)

    def test_symbolic_link_is_never_imported_as_an_ordinary_file(self):
        stream=io.BytesIO();info=zipfile.ZipInfo('data/link');info.create_system=3
        info.external_attr=(stat.S_IFLNK|0o777)<<16
        with zipfile.ZipFile(stream,'w') as archive:archive.writestr(info,'../../outside')
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            with self.assertRaisesRegex(ValueError,'ссылку'):inventory.verify(archive)

    def test_every_member_is_bound_including_metadata_and_config(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            archive.writestr('data/project/.git/objects/file',b'git-object')
            archive.writestr('data/project/empty/',b'')
            archive.writestr('helene.json',b'{}');receipt=inventory.seal(archive)
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            self.assertEqual(inventory.verify(archive,required=True)['members'],3)
            self.assertEqual(receipt['members'],3)
            entries=[(info,archive.read(info)) for info in archive.infolist()]
        for mutate in ('payload','mode','omitted','extra'):
            broken=io.BytesIO()
            with zipfile.ZipFile(broken,'w') as archive:
                for info,data in entries:
                    if info.filename=='helene.json':
                        if mutate=='omitted':continue
                        if mutate=='payload':data=b'{"changed":true}'
                        if mutate=='mode':info=zipfile.ZipInfo(info.filename);info.external_attr=0o777<<16
                    archive.writestr(info,data)
                if mutate=='extra':archive.writestr('data/unlisted',b'extra')
            broken.seek(0)
            with self.subTest(mutate=mutate),zipfile.ZipFile(broken) as archive:
                with self.assertRaises(ValueError):inventory.verify(archive,required=True)

    def test_legacy_has_no_invented_integrity_and_required_inventory_cannot_disappear(self):
        with self.archive([('data/story',b'old')]) as archive:
            self.assertEqual(inventory.verify(archive),{'verified':False,'legacy':True})
            with self.assertRaisesRegex(ValueError,'опись'):inventory.verify(archive,required=True)

    def test_export_import_and_tampering_leave_old_destination_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';target=root/'target'
            for folder in (source,target):
                (folder/'data').mkdir(parents=True)
                (folder/'helene.json').write_text('{"tree":"data"}',encoding='utf8')
            (source/'data/story').write_bytes(b'new life');(target/'data/story').write_bytes(b'old life')
            exported=carry.export(source/'helene.json',root/'source.zip')
            self.assertEqual(exported['inventory'],inventory.NAME)
            with zipfile.ZipFile(root/'source.zip') as original,zipfile.ZipFile(root/'broken.zip','w') as broken:
                for info in original.infolist():
                    broken.writestr(info,b'wrong' if info.filename=='data/story' else original.read(info))
            with self.assertRaises(ValueError):carry.import_(target/'helene.json',root/'broken.zip')
            self.assertEqual((target/'data/story').read_bytes(),b'old life')
            self.assertFalse(list(target.glob('data.before-*')))
            receipt=carry.import_(target/'helene.json',root/'source.zip')
            self.assertTrue(receipt['integrity']['verified'])
            self.assertEqual((target/'data/story').read_bytes(),b'new life')
            self.assertEqual((Path(receipt['backup'])/'story').read_bytes(),b'old life')


if __name__=='__main__':unittest.main()
