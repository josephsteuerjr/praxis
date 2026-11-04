import unittest
from xtts_miniapp_patch import assets


class MiniAppPatch(unittest.TestCase):
    def test_preserves_existing_bundle_and_updates_legacy_version_watch_identity(self):
        import re
        source='<head><script type="module" crossorigin src="/assets/index-old.js"></script></head><body></body>'
        index, files=assets(source,b'exact old bytes',b'extension',b'scoped css')
        alias=next(name for name in files if name.startswith('index-'))
        self.assertEqual(files[alias],b'exact old bytes')
        self.assertEqual(re.search(r'assets/index-[A-Za-z0-9_-]+\.js',index).group(0),'assets/'+alias)
        self.assertIn('data-praxis-voice="legacy"',index)
        changed,_=assets(source,b'exact old bytes',b'extension2',b'scoped css')
        self.assertNotEqual(index,changed)
        with self.assertRaises(ValueError):assets(index,b'',b'',b'')

    def test_unknown_entrypoint_is_rejected_before_mutation(self):
        for index in ['<head></head><body></body>','<script type="module" src="../../assets/index-a.js"></script>']:
            with self.subTest(index=index),self.assertRaises(ValueError):assets(index,b'',b'',b'')
