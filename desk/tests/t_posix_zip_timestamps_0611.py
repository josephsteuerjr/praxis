"""Deterministic tar inputs with epoch timestamps remain valid portable ZIPs."""
from pathlib import Path
import os, stat, sys, tempfile, unittest, zipfile
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'installer')]
from python_posix_bundle import write_program_archive


class PortableZipDates(unittest.TestCase):
    def test_epoch_source_preserves_bytes_and_executable_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); program = root / 'Helene'; program.mkdir()
            entry = program / 'helene'; entry.write_bytes(b'#!/bin/sh\nexit 0\n')
            entry.chmod(0o755); os.utime(entry, (0, 0))
            before = entry.read_bytes(), entry.stat().st_mtime_ns, stat.S_IMODE(entry.stat().st_mode)
            archive = write_program_archive(program, root / 'release.zip')
            with zipfile.ZipFile(archive) as z:
                self.assertIsNone(z.testzip())
                self.assertEqual(z.read('Helene/helene'), before[0])
                self.assertEqual(z.getinfo('Helene/helene').date_time, (1980, 1, 1, 0, 0, 0))
                if os.name != 'nt': self.assertEqual(z.getinfo('Helene/helene').external_attr >> 16 & 0o777, 0o755)
            self.assertEqual((entry.read_bytes(), entry.stat().st_mtime_ns, stat.S_IMODE(entry.stat().st_mode)), before)
            with self.assertRaises(FileExistsError): write_program_archive(program, archive)

    def test_archive_cannot_include_itself(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError): write_program_archive(root, root / 'recursive.zip')
            self.assertFalse((root / 'recursive.zip').exists())


if __name__ == '__main__': unittest.main()
