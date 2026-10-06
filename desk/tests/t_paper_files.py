"""Real local file IO, cancellation-independent atomicity and owner/local route gates."""
import asyncio
import base64
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deskd import local_files
import deskapp

class PaperFiles(unittest.TestCase):
    def test_save_conflict_replacement_name_and_exact_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / "media/artifacts/digest/отчёт.txt"
            source.parent.mkdir(parents=True); source.write_bytes(b"first\x00\xff")
            folder = root / "destination"; folder.mkdir()
            result = local_files.save_file(root, source.relative_to(root).as_posix(), str(folder), "копия.txt")
            self.assertEqual(Path(result["path"]).read_bytes(), source.read_bytes())
            with self.assertRaises(FileExistsError): local_files.save_file(root, source.relative_to(root).as_posix(), str(folder), "копия.txt")
            source.write_bytes(b"second")
            local_files.save_file(root, source.relative_to(root).as_posix(), str(folder), "копия.txt", True)
            self.assertEqual((folder / "копия.txt").read_bytes(), b"second")
            local_files.save_file(root, source.relative_to(root).as_posix(), str(folder), "preview.txt", data=base64.b64encode(b"admitted preview bytes").decode())
            self.assertEqual((folder / "preview.txt").read_bytes(), b"admitted preview bytes")
            self.assertEqual(list(folder.glob(".helene-save-*")), [])
            for name in ("../bad", "CON.txt", "bad.", "bad\x00x"):
                with self.assertRaises(ValueError): local_files.save_file(root, source.relative_to(root).as_posix(), str(folder), name)
            with self.assertRaises(ValueError): local_files.save_file(root, "../outside", str(folder), "out.txt")
            with patch.object(local_files, "MAX_FILE", 2):
                with self.assertRaises(ValueError): local_files.save_file(root, source.relative_to(root).as_posix(), str(folder), "out.txt")
            self.assertFalse((folder / "out.txt").exists())

    def test_read_arbitrary_document_and_directory_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "Папка").mkdir(); p = root / "данные.docx"; p.write_bytes(b"PK\x00\xff")
            rows = local_files.listing(str(root))["entries"]
            self.assertTrue(rows[0]["folder"])
            value = local_files.read_file(str(p)); self.assertEqual(base64.b64decode(value["data"]), p.read_bytes())
            with patch.object(local_files, "MAX_FILE", 2):
                with self.assertRaises(ValueError): local_files.read_file(str(p))
            with self.assertRaises(ValueError): local_files.listing("relative")

    def test_route_does_not_expose_server_or_device_files(self):
        class Call:
            role = "device"; local = True; body = None; query = {}
        c = Call()
        with self.assertRaises(deskapp.web.HTTPForbidden): asyncio.run(deskapp._r_local_files(c))
        c.role = "owner"; c.local = False
        with self.assertRaises(deskapp.web.HTTPForbidden): asyncio.run(deskapp._r_local_files(c))
        c.local = True
        with patch.dict("os.environ", {"HELENE_SERVER_AUTH_IMPORT": "1"}):
            with self.assertRaises(deskapp.web.HTTPForbidden): asyncio.run(deskapp._r_local_files(c))

if __name__ == "__main__": unittest.main()
