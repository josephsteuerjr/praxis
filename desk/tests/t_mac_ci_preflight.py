"""Mac compilation starts only after the matching Windows release is available."""
import copy
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / 'installer/mac_ci_preflight.py'
spec = importlib.util.spec_from_file_location('mac_ci_preflight', path)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class MacReleasePreflightTests(unittest.TestCase):
    def release(self, tag='v7.8.9'):
        version = tag.lstrip('vV')
        return {'tag_name': tag, 'assets': [
            {'name': f'Helene-{version}.zip', 'state': 'uploaded', 'size': 42},
            {'name': f'Helene-{version}.zip.sha256', 'state': 'uploaded', 'size': 80},
        ]}

    def test_matching_release_passes(self):
        self.assertEqual(preflight.check_release(self.release(), 'v7.8.9'), 'Helene-7.8.9.zip')

    def test_old_assets_under_new_tag_are_rejected(self):
        release = self.release('v7.8.8')
        release['tag_name'] = 'v7.8.9'
        with self.assertRaisesRegex(ValueError, 'Windows'):
            preflight.check_release(release, 'v7.8.9')

    def test_checksum_missing_or_upload_incomplete_is_rejected(self):
        for mutation in ('checksum', 'empty', 'uploading', 'duplicate'):
            with self.subTest(mutation=mutation):
                release = self.release()
                if mutation == 'checksum':
                    release['assets'].pop()
                elif mutation == 'empty':
                    release['assets'][0]['size'] = 0
                elif mutation == 'uploading':
                    release['assets'][0]['state'] = 'uploading'
                else:
                    release['assets'].append(copy.deepcopy(release['assets'][0]))
                with self.assertRaises(ValueError):
                    preflight.check_release(release, 'v7.8.9')

    def test_mismatched_tag_and_invalid_tag_are_rejected(self):
        for tag in ('v7.8.8', 'v7.8.9; command'):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                preflight.check_release(self.release(), tag)


if __name__ == '__main__':
    unittest.main()
