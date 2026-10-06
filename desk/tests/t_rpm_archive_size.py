"""Unsigned metadata repair preserves the main header/payload; signed refusal."""
import gzip
import struct
import sys
import tempfile
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "installer"))
from rpm_archive_size import repair

def header(entries, data):
    return b"\x8e\xad\xe8\x01" + b"\0" * 4 + struct.pack(">II", len(entries), len(data)) + b"".join(struct.pack(">IIII", *entry) for entry in entries) + data

class RPMSize(unittest.TestCase):
    def test_updates_only_size_and_refuses_signatures(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.rpm"
            for signed in (False, True):
                signature = header([(1007, 4, 0, 1)] + ([(1005, 7, 4, 1)] if signed else []), struct.pack(">I", 1) + (b"x" if signed else b""))
                prefix = b"\xed\xab\xee\xdb" + b"\0" * 92 + signature
                prefix += b"\0" * (-len(prefix) % 8)
                main = header([(1125, 6, 0, 1)], b"gzip\0")
                payload = gzip.compress(b"archive" * 100)
                before = prefix + main + payload
                path.write_bytes(before)
                if signed:
                    with self.assertRaisesRegex(ValueError, "signed"):
                        repair(path)
                    self.assertEqual(path.read_bytes(), before)
                else:
                    result = repair(path)
                    self.assertEqual(result["decoded_bytes"], 700)
                    self.assertEqual(path.read_bytes()[len(prefix):], main + payload)
                    self.assertEqual(repair(path)["previous_sizes"], [700])

if __name__ == "__main__":
    unittest.main()
