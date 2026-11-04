import hashlib
import unittest
from fish_miniapp_patch import assets


class PatchCase(unittest.TestCase):
    def test_both_existing_entry_prefixes_preserve_exact_base_bytes(self):
        base=b'original-entry\x00bytes'
        for prefix in ('./assets/','/m/assets/'):
            with self.subTest(prefix=prefix):
                index='<html><head><script type="module" crossorigin src="'+prefix+'index-original.js"></script></head><body></body></html>'
                updated,files=assets(index,base,b'fish-module',b'fish-style')
                self.assertIn('data-praxis-fish="v1"',updated)
                aliases=[n for n in files if n.startswith('index-')]
                self.assertEqual(len(aliases),1);self.assertEqual(files[aliases[0]],base)
                self.assertIn(prefix+aliases[0],updated)
                self.assertIn(prefix+next(n for n in files if n.endswith('.css')),updated)
                self.assertEqual(len(files),3)

    def test_unknown_or_already_changed_page_is_not_overwritten(self):
        for index in ('<html></html>','<script data-praxis-fish="v1"></script>',
                      '<script type="module" src="../assets/index-old.js"></script>'):
            with self.assertRaises(ValueError):assets(index,b'old',b'new',b'css')

    def test_new_module_bytes_create_new_cache_identity(self):
        index='<head><script type="module" src="./assets/index-old.js"></script></head><body></body>'
        a,filesa=assets(index,b'base',b'a',b'css');b,filesb=assets(index,b'base',b'b',b'css')
        self.assertNotEqual(a,b);self.assertTrue(set(filesa).isdisjoint(filesb))


if __name__=='__main__':unittest.main()
