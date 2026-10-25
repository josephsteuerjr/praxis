from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

INSTALLER = Path(__file__).resolve().parents[1] / 'installer'
sys.path.insert(0, str(INSTALLER))
import compose_python_edition


class CompositionTests(unittest.TestCase):
    def test_selected_core_worker_is_not_replaced_with_git_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            worker = root / 'praxis/forge_worker.py'
            worker.parent.mkdir()
            worker.write_bytes(b'current read-only worker')
            listing = b'100644 blob worker\tpraxis/forge_worker.py\0'
            def git(command, **kwargs):
                if command[1] == 'ls-tree':
                    return listing
                if command[1] == 'rev-parse':
                    return b'head\n'
                self.fail('Selected worker must use current bytes')
            with patch.object(compose_python_edition.subprocess, 'check_output', side_effect=git):
                record = compose_python_edition.compose(root, root / 'edition', ['praxis/forge_worker.py'])
            self.assertEqual((root / 'edition/forge_worker.py').read_bytes(), worker.read_bytes())
            self.assertEqual(record['files']['forge_worker.py']['source'], 'praxis/forge_worker.py')

    def test_private_traversal_and_duplicate_destination_inputs_refused(self):
        for selections in (['praxis/../AGENTS.md'], ['praxis/memory/private.py'], ['praxis/forge.py', 'helene/core/forge.py']):
            with self.subTest(selections=selections), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for name in selections:
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'not to package')
                with patch.object(compose_python_edition.subprocess, 'check_output', return_value=b''):
                    with self.assertRaises(ValueError):
                        compose_python_edition.compose(root, root / 'edition', selections)
                self.assertFalse((root / 'edition').exists())


if __name__ == '__main__':
    unittest.main()
