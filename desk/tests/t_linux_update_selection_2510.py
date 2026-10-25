from pathlib import Path
import json
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import python_platform as platform, python_updates as updates

class LinuxUpdateSelection(unittest.TestCase):
    def test_published_fedora_marker_selects_fedora_archive_and_digest(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'python-app.json').write_text('{"version":"1.9.4","target":"linux-x86_64"}')
            (root/'FEDORA-BUILD.json').write_text('{}')
            release={'tag_name':'v2.0.0','assets':[{'name':n,'browser_download_url':'https://example.invalid/'+n,'digest':'sha256:'+h*64} for n,h in [('Helene-2.0.0-linux-x86_64.zip','a'),('Helene-2.0.0-fedora-x86_64.zip','b')]]}
            with patch.object(updates,'request_json',return_value=release):
                value=updates.update_check(SimpleNamespace(root=root),'https://example.invalid/latest')
            self.assertTrue(value['url'].endswith('fedora-x86_64.zip'));self.assertEqual(value['sha256'],'b'*64)
            (root/'FEDORA-BUILD.json').unlink()
            with patch.object(updates,'request_json',return_value=release):
                value=updates.update_check(SimpleNamespace(root=root),'https://example.invalid/latest')
            self.assertTrue(value['url'].endswith('linux-x86_64.zip'));self.assertEqual(value['sha256'],'a'*64)

    def test_cross_distribution_install_and_download_refuse_before_network_or_overlay(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);old=base/'fedora';new=base/'debian';old.mkdir();new.mkdir()
            for p in (old,new):(p/'python-app.json').write_text('{"version":"2.0.0","target":"linux-x86_64"}')
            (old/'FEDORA-BUILD.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'another Linux distribution'):platform.require_runtime(new,old)
            with patch.object(updates,'_download') as download:
                with self.assertRaisesRegex(ValueError,'application target'):updates.update_download(SimpleNamespace(root=old),'https://example.invalid/Helene-2.0.1-linux-x86_64.zip')
                download.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
