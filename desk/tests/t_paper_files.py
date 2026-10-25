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

    def test_owner_http_route_uses_the_same_python_file_operations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "media/artifacts/abc/document.bin"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"source\x00\xff")
            folder = root / "destination"
            folder.mkdir()

            class Call:
                role = "owner"
                local = True
                body = None
                query = {"path": str(folder)}

            c = Call()
            with patch.object(deskapp.readers, "tree", return_value=root):
                self.assertEqual(asyncio.run(deskapp._r_local_files(c))["entries"], [])
                c.body = {"action": "save", "rel": source.relative_to(root).as_posix(),
                          "folder": str(folder), "name": "copy.bin", "overwrite": False,
                          "data": base64.b64encode(b"edited\x00").decode()}
                saved = asyncio.run(deskapp._r_local_files(c))
                self.assertEqual(saved["bytes"], 7)
                self.assertEqual((folder / "copy.bin").read_bytes(), b"edited\x00")
                c.body = {"action": "read", "path": str(folder / "copy.bin")}
                read = asyncio.run(deskapp._r_local_files(c))
                self.assertEqual(base64.b64decode(read["data"]), b"edited\x00")
                c.body = {"action": "save", "rel": source.relative_to(root).as_posix(),
                          "folder": str(folder), "name": "copy.bin", "overwrite": False}
                with self.assertRaises(deskapp.web.HTTPConflict):
                    asyncio.run(deskapp._r_local_files(c))
                self.assertEqual((folder / "copy.bin").read_bytes(), b"edited\x00")

                # A lost confirmation AFTER replace does not prove the old
                # bytes survived. HTTP tells the owner to read back, never
                # presents a precommit 400 or silently resends the write.
                real_replace = local_files.os.replace
                def committed_then_lost(src, dst):
                    real_replace(src, dst)
                    raise OSError("postcommit reply lost")
                c.body["overwrite"] = True
                with patch.object(local_files.os, "replace", side_effect=committed_then_lost):
                    with self.assertRaises(deskapp.web.HTTPServiceUnavailable) as raised:
                        asyncio.run(deskapp._r_local_files(c))
                self.assertIn("перед повтором", raised.exception.text)
                self.assertEqual((folder / "copy.bin").read_bytes(), source.read_bytes())

if __name__ == "__main__": unittest.main()
