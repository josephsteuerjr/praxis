"""Real bytes: initial message -> durable files, protected artifact HTTP route."""
import base64
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / "localharness")]
import runner
import deskapp
from deskd import artifacts, readers
from localharness.transport import Desk
from aiohttp.test_utils import TestClient, TestServer


class InitialFiles(unittest.TestCase):
    def test_first_idle_message_has_real_files_before_model_and_keeps_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            source = tree / "memory/.control/desk_inbox/attachments/one/about.md"
            source.parent.mkdir(parents=True)
            source.write_text("document", encoding="utf8")
            desk = Desk(tree, "window", "owner", "Chat")
            current = SimpleNamespace(run_id="run-test", delivery_chat_id="window")
            agent = SimpleNamespace(_runs=lambda: SimpleNamespace(path=lambda _: tree / "memory/runs/2026-10/run-test"))
            def turn(*args, **kw):
                with patch("turn_inbox.collect", return_value=([], lambda: None)):
                    batch, _ = runner._drain_owner_inputs(current, [])
                copied = tree / "memory/runs/2026-10/run-test/files/about.md"
                self.assertEqual(copied.read_text(), "document")
                self.assertIn(str(copied), batch[0]["content"])
                with patch("turn_inbox.collect", return_value=([], lambda: None)):
                    self.assertEqual(runner._drain_owner_inputs(current, batch)[0], [])
            with patch.object(runner, "_tree", tree), patch.object(runner, "_agent", agent), patch.object(runner, "_room", return_value=desk), patch.object(runner, "_turn_in_window", side_effect=turn):
                runner.handle_desk("Read it", attachments=["attachments/one/about.md"], ingress_id="one")
                first = runner._batch_files(["attachments/one/about.md"], run_id="run-test")
                source.write_text("different", encoding="utf8")
                second = runner._batch_files(["attachments/one/about.md"], run_id="run-test")
                self.assertNotEqual(first, second)
                self.assertEqual((tree / "memory/runs/2026-10/run-test/files/about.md").read_text(), "document")
            self.assertTrue(source.is_file())
            rows = [json.loads(row) for row in (tree / "memory/groups/window.jsonl").read_text().splitlines()]
            self.assertEqual(rows[-1]["media_name"], "about.md")
            self.assertEqual(rows[-1]["media_kind"], "file")
            self.assertIsNone(runner._INITIAL_FILES.get())

    def test_artifact_snapshots_external_bytes_and_rejects_secret_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            tree.mkdir()
            source = Path(tmp) / "report.xlsx"
            source.write_bytes(b"original")
            rel = artifacts.stage(tree, source)
            source.write_bytes(b"changed")
            path, _ = artifacts.resolve(tree, rel)
            self.assertEqual(path.read_bytes(), b"original")
            for rel in ["../report.xlsx", "helene.json", "media/artifacts/../../helene.json", str(source)]:
                self.assertIsNone(artifacts.resolve(tree, rel)[0])


class FileHTTP(unittest.IsolatedAsyncioTestCase):
    async def test_upload_over_one_megabyte_and_download_artifact_with_real_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            raw = b"# notes\n" * 180000
            with patch.object(readers, "tree", return_value=tree), patch.object(readers, "reader_status", return_value={"alive": True}), patch.object(deskapp, "TOKEN", "test-owner"), patch.object(deskapp, "_host_ok", return_value=True), patch.object(deskapp, "_origin_ok", return_value=(True, "")):
                client = TestClient(TestServer(deskapp.build_app()))
                await client.start_server()
                try:
                    response = await client.post("/api/say", headers={"Authorization": "Bearer test-owner"}, json={"text": "Read", "attachments": [{"name": "about.md", "mime": "text/markdown", "data": base64.b64encode(raw).decode()}]})
                    self.assertEqual(response.status, 200, await response.text())
                    source = next((tree / "memory/.control/desk_inbox/attachments").rglob("*.md"))
                    self.assertEqual(source.read_bytes(), raw)
                    rel = source.relative_to(tree).as_posix()
                    response = await client.get("/api/artifact", params={"path": rel, "preview": "1"}, headers={"Authorization": "Bearer test-owner"})
                    self.assertEqual(response.status, 200)
                    self.assertEqual(await response.read(), raw)
                    self.assertTrue(response.headers["Content-Type"].startswith("text/plain"))
                    self.assertIn("inline", response.headers["Content-Disposition"])
                    image = tree / "workspace/media/outbound/owner/chat-one/generated.png"
                    image.parent.mkdir(parents=True)
                    image.write_bytes(b"\x89PNG\r\n\x1a\nactual")
                    response = await client.get("/api/media", params={"path": image.relative_to(tree).as_posix()}, headers={"Authorization": "Bearer test-owner"})
                    self.assertEqual(response.status, 200)
                    self.assertEqual(await response.read(), image.read_bytes())
                    legacy = tree / "media/screenshots/shot.png"
                    legacy.parent.mkdir(parents=True)
                    legacy.write_bytes(image.read_bytes())
                    response = await client.get("/api/artifact", params={"path": legacy.relative_to(tree).as_posix(), "preview":"1"}, headers={"Authorization":"Bearer test-owner"})
                    self.assertEqual(response.status, 200)
                    self.assertEqual(await response.read(), legacy.read_bytes())
                    self.assertTrue(response.headers["Content-Type"].startswith("image/png"))
                    response = await client.get("/api/artifact", params={"path": rel})
                    self.assertEqual(response.status, 403)
                finally:
                    await client.close()


if __name__ == "__main__":
    unittest.main()
