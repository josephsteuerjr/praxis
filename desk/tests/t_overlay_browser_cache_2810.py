from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import update_overlay as overlay

class BrowserCache(unittest.TestCase):
    def test_locked_and_changing_setup_cache_never_enters_overlay(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source';target=root/'target';target.mkdir();source.mkdir()
            (source/'app.py').write_text('new');(target/'app.py').write_text('old')
            cache=source/'.migration-webview/EBWebView/Default/Cache/Cache_Data/data_0'
            cache.parent.mkdir(parents=True);cache.write_bytes(b'locked cache')
            (source/'.python-migrator-123.json').write_text('{}')
            original=Path.open
            def locked(path,*args,**kwargs):
                if '.migration-webview' in path.parts:raise PermissionError(13,'Browser cache locked',str(path))
                return original(path,*args,**kwargs)
            with patch.object(Path,'open',locked):
                from localharness.source_merge import inventory
                self.assertEqual(set(inventory(source)),{'app.py'})
                result=overlay.prepare(source,target,root/'recovery')
                with original(cache,'wb') as stream:stream.write(b'cache changed by browser')
                self.assertEqual(set(result['inventory']),{'app.py'})
                overlay.validate(source,target,root/'recovery')
            self.assertEqual((target/'app.py').read_text(),'old')
            (source/'app.py').write_text('late change')
            with self.assertRaises((ValueError,IOError)):overlay.validate(source,target,root/'recovery')

if __name__=='__main__':unittest.main()
