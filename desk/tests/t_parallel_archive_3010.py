"""Actual concurrent tiny ZIP extraction, complete byte proofs and failure isolation."""
from pathlib import Path
import hashlib,os,sys,tempfile,threading,unittest,zipfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import parallel_files as files

class ParallelArchive(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='helene-archive-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.archive=self.root/'incoming.zip';self.target=self.root/'out';self.target.mkdir()

    def test_twelve_tiny_file_streams_overlap_and_mixed_payloads_have_independent_proofs(self):
        workers=12
        payload={f'Helene/runtime/small-{i}.py':f'VALUE={i}\n'.encode() for i in range(workers+3)}
        payload['Helene/runtime/large.bin']=bytes(range(256))*2048
        with zipfile.ZipFile(self.archive,'w',zipfile.ZIP_DEFLATED) as writer:
            for name,body in payload.items():writer.writestr(name,body)
        barrier=threading.Barrier(workers);lock=threading.Lock();seen=set();original=zipfile.ZipFile.open
        def opened(reader,entry,*args,**kwargs):
            identity=threading.get_ident()
            with lock:first=identity not in seen;seen.add(identity)
            if first:barrier.wait(timeout=10)
            return original(reader,entry,*args,**kwargs)
        with patch.object(zipfile.ZipFile,'open',opened):verified=files.extract(self.archive,self.target,workers=workers)
        self.assertEqual(len(seen),workers)
        self.assertEqual(set(verified),{str(self.target/name) for name in payload})
        for name,expected in payload.items():
            actual=self.target/name
            self.assertEqual(actual.read_bytes(),expected)
            self.assertEqual(verified[str(actual)]['sha256'],hashlib.sha256(expected).hexdigest())
        self.assertFalse(list(self.target.rglob('*.part')))

    def test_alias_and_collision_admission_precedes_all_output(self):
        for names in (('Helene/a','Helene/a/b'),('Helene/a','Helene/../outside')):
            with self.subTest(names=names):
                with zipfile.ZipFile(self.archive,'w') as writer:
                    for name in names:writer.writestr(name,b'payload')
                with self.assertRaises(ValueError):files.extract(self.archive,self.target,workers=12)
                self.assertEqual(list(self.target.iterdir()),[])

    def test_corrupt_member_keeps_previous_file_and_removes_temporary_output(self):
        name='Helene/runtime/unchanged.py';old=self.target/name;old.parent.mkdir(parents=True);old.write_bytes(b'previous')
        with zipfile.ZipFile(self.archive,'w',zipfile.ZIP_STORED) as writer:writer.writestr(name,b'new member')
        with zipfile.ZipFile(self.archive) as reader:entry=reader.getinfo(name)
        body=bytearray(self.archive.read_bytes());offset=entry.header_offset+30+len(entry.filename.encode())+len(entry.extra);body[offset]^=1
        self.archive.write_bytes(body)
        with self.assertRaises(zipfile.BadZipFile):files.extract(self.archive,self.target,workers=12)
        self.assertEqual(old.read_bytes(),b'previous');self.assertFalse(list(self.target.rglob('*.part')))

    def test_existing_link_cannot_redirect_any_worker(self):
        outside=self.root/'outside';outside.mkdir();(self.target/'Helene').mkdir()
        try:(self.target/'Helene/runtime').symlink_to(outside,target_is_directory=True)
        except OSError as exc:self.skipTest(str(exc))
        with zipfile.ZipFile(self.archive,'w') as writer:writer.writestr('Helene/runtime/escape.py',b'bad')
        with self.assertRaises(ValueError):files.extract(self.archive,self.target,workers=12)
        self.assertEqual(list(outside.iterdir()),[])

if __name__=='__main__':unittest.main()
