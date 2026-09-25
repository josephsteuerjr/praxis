"""26.09 — чтение улик прогонов в издании на Windows (ревью W2 S1, blocker).

Её строгий читатель (`run_retention.safe_evidence_stream`) держит ворота на POSIX
`openat`/`O_NOFOLLOW`/`dir_fd`. После закрытия отставания (1.0.1) через него пошло ВСЁ
чтение результатов `run_manager`, и на нативной Windows каждое чтение отказывало: ход не
закрывал прогон, её журнал писал «без реплики», рука `read_run_result` и возобновление не
читали результатов. Гейт шёл только в Linux и этого не видел.

Что прибито:
* ветка Windows читает обычный файл под `…/runs/…`, отказывает на файле с двумя жёсткими
  ссылками, на ссылке/junction в пути под корнем прогонов; нет последнего звена —
  `EvidenceNotFound`, нет предка — `ContractError`;
* на Windows `read_result` читает горячий результат, а снятое ретенцией тело — это
  `RunNotFound`, а не «платформа не поддерживает».

Ветка — чистый питон, поэтому её проверки идут и в Linux-гейте (кроме junction).
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import run_retention as rr
from run_context import RunContext
from run_manager import RunManager, RunNotFound


class WindowsEvidenceBranch(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.results = self.root / "memory" / "runs" / "2026-09" / "run-x" / "results"
        self.results.mkdir(parents=True)
        self.body = self.results / "0001-tool.log"
        self.body.write_bytes(b"line 1\nline 2\n")

    def read(self, path: Path) -> bytes:
        with rr._windows_evidence_stream(path) as stream:
            return stream.read()

    def test_regular_file_is_read(self):
        self.assertEqual(self.read(self.body), b"line 1\nline 2\n")

    def test_hardlinked_file_is_refused(self):
        try:
            os.link(self.body, self.results / "0002-twin.log")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"hard links unavailable: {exc}")
        with self.assertRaises(rr.ContractError):
            self.read(self.body)

    def test_missing_final_component_is_evidence_not_found(self):
        with self.assertRaises(rr.EvidenceNotFound):
            self.read(self.results / "0009-gone.log")

    def test_missing_ancestor_is_not_evidence_not_found(self):
        with self.assertRaises(rr.ContractError) as caught:
            self.read(self.results.parent.parent / "run-y" / "results" / "0001-tool.log")
        self.assertNotIsInstance(caught.exception, rr.EvidenceNotFound)

    def test_parent_traversal_is_refused(self):
        with self.assertRaises(rr.ContractError):
            self.read(Path(str(self.results)) / ".." / "results" / "0001-tool.log")

    def test_symlink_under_runs_is_refused(self):
        outside = self.root / "outside.log"
        outside.write_bytes(b"secret")
        link = self.results / "0003-link.log"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaises(rr.ContractError):
            self.read(link)

    @unittest.skipUnless(os.name == "nt", "junction — только Windows")
    def test_junction_under_runs_is_refused(self):
        import _winapi
        target = self.root / "elsewhere"
        (target / "results").mkdir(parents=True)
        (target / "results" / "0001-tool.log").write_bytes(b"planted")
        junction = self.results.parent.parent / "run-j"
        _winapi.CreateJunction(str(target), str(junction))
        with self.assertRaises(rr.ContractError):
            self.read(junction / "results" / "0001-tool.log")

    @unittest.skipUnless(os.name == "nt", "ветка включается только на Windows")
    def test_public_stream_uses_the_windows_branch(self):
        with rr.safe_evidence_stream(self.body) as stream:
            self.assertEqual(stream.read(), b"line 1\nline 2\n")


@unittest.skipUnless(os.name == "nt", "сквозной путь Windows")
class ReadResultOnWindows(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.manager = RunManager(Path(tmp.name))
        context = RunContext(run_id="run-20260926T000000-win", kind="chat_turn", goal="probe",
                             principal_id="telegram:1", scope="owner",
                             created_at="2026-09-26T00:00:00Z")
        self.manager.create(context, "# probe\n")
        self.manager.transition(context.run_id, "running", expected="pending")
        self.ref = self.manager.store_result(context.run_id, "ответ хода\n", name="tool")
        self.run_id = context.run_id

    def test_hot_result_is_read(self):
        page = self.manager.read_result(self.run_id, self.ref["result_id"])
        self.assertIn("ответ хода", str(page))

    def test_body_removed_by_retention_is_not_found_not_a_platform_error(self):
        run_dir = next(Path(self.manager.root).rglob(self.run_id))
        (run_dir / self.ref["path"]).unlink()
        with self.assertRaises(RunNotFound):
            self.manager.read_result(self.run_id, self.ref["result_id"])


if __name__ == "__main__":       # pragma: no cover
    unittest.main(verbosity=2)
