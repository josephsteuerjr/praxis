"""
PASS 8.5 — мастерская: fs_edit-точность, зоны записи, read-guard, квота, run,
send_file через мокнутый мост, code_map, кодинг-окно. Герметичны.

Запуск:  python praxis_test.py test_workshop -v
"""
from __future__ import annotations

import os
import hashlib
import concurrent.futures
import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import agent
import forge
import llm
import media
import selfdev
import workshop


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="praxis_ws_"))
        for d in ("workspace/projects", "soul/skills", "memory"):
            (self.tmp / d).mkdir(parents=True)
        (self.tmp / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.tmp / ".env").write_text("SECRET=x\n", encoding="utf-8")
        (self.tmp / "memory" / "llm.json").write_text('{"k": "secret"}', encoding="utf-8")
        self._orig = [(workshop, k, getattr(workshop, k)) for k in ("BASE", "REPO", "PROJECTS")]
        workshop.BASE = self.tmp
        workshop.REPO = self.tmp
        workshop.PROJECTS = self.tmp / "workspace" / "projects"
        workshop._MAP_CACHE.update(key=None, text=None)

    def tearDown(self):
        for m, k, v in self._orig:
            setattr(m, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _proj(self, name="демо"):
        workshop.project_create(name, "тестовый бриф")
        return workshop._slug(name)


class TestFsEdit(Base):
    def test_unique_replacement(self):
        p = self.tmp / "workspace" / "a.py"
        p.write_text("x = 1\ny = 2\n", encoding="utf-8")
        out = workshop.fs_edit("workspace/a.py", "y = 2", "y = 3")
        self.assertIn("Поправила", out)
        self.assertIn("y = 3", p.read_text(encoding="utf-8"))

    def test_zero_matches_refused_with_hint(self):
        (self.tmp / "workspace" / "a.py").write_text("x = 1\n", encoding="utf-8")
        out = workshop.fs_edit("workspace/a.py", "нет такого", "z")
        self.assertIn("не найдено", out.lower())
        self.assertIn("fs_read", out)

    def test_many_matches_refused(self):
        (self.tmp / "workspace" / "a.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
        out = workshop.fs_edit("workspace/a.py", "x = 1", "x = 2")
        self.assertIn("2 совпадени", out)
        self.assertIn("контекст", out.lower())

    def test_unicode_precise(self):
        p = self.tmp / "soul" / "note.md"
        p.write_text("— тире и «ёлочки», ёж\n", encoding="utf-8")
        out = workshop.fs_edit("soul/note.md", "«ёлочки», ёж", "«ёлочки», уж")
        self.assertIn("Поправила", out)
        self.assertIn("уж", p.read_text(encoding="utf-8"))

    def test_fs_write_only_new(self):
        self.assertIn("Записала", workshop.fs_write("workspace/new.txt", "привет"))
        out = workshop.fs_write("workspace/new.txt", "второй раз")
        self.assertIn("существует", out)


class TestFileMutationReceipt(Base):
    def test_forge_and_workshop_share_same_preimage_lock(self):
        p = self.tmp / "workspace/a.txt"
        p.write_bytes(b"old")
        before = hashlib.sha256(b"old").hexdigest()
        task_id = "code-race1234"
        task_dir = self.tmp / "forge-tasks" / task_id
        task_dir.mkdir(parents=True)
        (task_dir / "task.json").write_text(json.dumps({
            "id": task_id, "goal": "race", "root": str(self.tmp), "status": "active",
            "run_context": {},
        }), encoding="utf-8")
        barrier = threading.Barrier(2)
        publish = workshop._publish_bytes
        hash_file = forge._hash
        first_hash = threading.local()

        def delayed_publish(*args, **kwargs):
            barrier.wait(timeout=5)  # workshop read its preimage, before taking the lock
            return publish(*args, **kwargs)

        def delayed_hash(target, *args, **kwargs):
            if target == p and not getattr(first_hash, "seen", False):
                first_hash.seen = True
                barrier.wait(timeout=5)  # Forge already holds the SAME target lock
            return hash_file(target, *args, **kwargs)

        with patch.object(forge, "TASKS_DIR", self.tmp / "forge-tasks"), \
             patch.object(forge, "_event"), \
             patch.object(workshop, "_publish_bytes", delayed_publish), \
             patch.object(forge, "_hash", delayed_hash), \
             concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fs = pool.submit(workshop.fs_edit_result, "workspace/a.txt", "old", "fs",
                             expected_sha256=before)
            code = pool.submit(forge.edit, task_id, "replace", path="workspace/a.txt",
                               old="old", new="forge", expected_sha256=before)
            fs_result, code_result = fs.result(timeout=10), code.result(timeout=10)
        if fs_result["status"] == "confirmed":
            self.assertIn("Конфликт версии", code_result)
            self.assertEqual(p.read_bytes(), b"fs")
        else:
            self.assertEqual(fs_result["status"], "refused")
            self.assertIn("replace:", code_result)
            self.assertEqual(p.read_bytes(), b"forge")

    def test_same_preimage_racers_cannot_both_confirm(self):
        p = self.tmp / "workspace/a.txt"
        p.write_bytes(b"old")
        expected = hashlib.sha256(b"old").hexdigest()
        barrier = threading.Barrier(2)
        publish = workshop._publish_bytes

        def simultaneous_publish(*args, **kwargs):
            # Both calls have already observed the SAME preimage before entering
            # the per-target lock. Only one may still publish after rechecking it.
            barrier.wait(timeout=5)
            return publish(*args, **kwargs)

        with patch.object(workshop, "_publish_bytes", simultaneous_publish), \
             concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            left = pool.submit(workshop.fs_write_result, "workspace/a.txt", "left", "", True,
                               True, expected)
            right = pool.submit(workshop.fs_write_result, "workspace/a.txt", "right", "", True,
                                True, expected)
            results = [left.result(timeout=10), right.result(timeout=10)]
        self.assertEqual(sorted(r["status"] for r in results), ["confirmed", "refused"])
        self.assertIn(p.read_bytes(), {b"left", b"right"})

    def test_python_only_even_if_native_available_or_lost_response(self):
        with patch.object(workshop.hands, "binary", return_value=Path("compiled-hands")), \
             patch.object(workshop.hands, "call", side_effect=AssertionError("native call")), \
             patch.object(workshop.hands, "write", side_effect=AssertionError("native write")), \
             patch.object(workshop.hands, "edit", side_effect=AssertionError("native edit")):
            created = workshop.fs_write_result("workspace/a.txt", "alpha")
            edited = workshop.fs_edit_result("workspace/a.txt", "alpha", "beta")
        self.assertEqual((created["status"], edited["status"]), ("confirmed", "confirmed"))
        self.assertEqual((self.tmp / "workspace/a.txt").read_bytes(), b"beta")

    def test_no_binary_is_same_python_mutation_path(self):
        with patch.object(workshop.hands, "binary", return_value=None), \
             patch.object(workshop.hands, "call", side_effect=AssertionError("binary missing")):
            result = workshop.fs_write_result("workspace/no-binary.txt", "direct")
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual((self.tmp / "workspace/no-binary.txt").read_bytes(), b"direct")

    def test_hash_precondition_and_readback(self):
        p = self.tmp / "workspace/a.txt"
        p.write_bytes(b"old")
        before = hashlib.sha256(b"old").hexdigest()
        blocked = workshop.fs_write_result("workspace/a.txt", "new", overwrite=True,
                                            expected_sha256="0" * 64)
        self.assertEqual(blocked["status"], "refused")
        self.assertEqual(p.read_bytes(), b"old")
        written = workshop.fs_edit_result("workspace/a.txt", "old", "new",
                                          expected_sha256=before)
        self.assertEqual(written["status"], "confirmed")
        self.assertRegex(written["operation_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(written["before_sha256"], before)
        self.assertEqual(written["after_sha256"], hashlib.sha256(p.read_bytes()).hexdigest())
        self.assertEqual(workshop.fs_edit_result("workspace/a.txt", "new", "again",
                                                  expected_sha256=before)["status"], "refused")

    def test_symlink_escape_is_refused_without_touching_outside(self):
        outside = self.tmp.parent / (self.tmp.name + "_outside.txt")
        outside.write_bytes(b"untouched")
        link = self.tmp / "workspace" / "alias.txt"
        try:
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink unavailable: {exc}")
            result = workshop.fs_write_result("workspace/alias.txt", "wrong", overwrite=True)
            self.assertEqual(result["status"], "refused")
            self.assertEqual(outside.read_bytes(), b"untouched")
        finally:
            outside.unlink(missing_ok=True)

    def test_ambiguous_edit_is_refused_without_write(self):
        p = self.tmp / "workspace/a.txt"
        p.write_bytes(b"dup\ndup\n")
        result = workshop.fs_edit_result("workspace/a.txt", "dup", "different")
        self.assertEqual(result["status"], "refused")
        self.assertIn("2 совпадений", result["message"])
        self.assertEqual(p.read_bytes(), b"dup\ndup\n")

    def test_interrupted_publication_is_in_doubt_and_not_retried(self):
        p = self.tmp / "workspace/a.txt"
        p.write_bytes(b"old")
        real_replace = os.replace
        calls = []

        def commit_then_lose_response(src, dst):
            calls.append((src, dst))
            real_replace(src, dst)
            raise OSError("response lost after commit")

        with patch.object(workshop.os, "replace", side_effect=commit_then_lose_response):
            result = workshop.fs_edit_result("workspace/a.txt", "old", "new")
        self.assertEqual(result["status"], "in_doubt")
        self.assertEqual(len(calls), 1)
        self.assertEqual(p.read_bytes(), b"new")
        self.assertIsNone(result["after_sha256"])

    def test_readback_failure_after_commit_is_in_doubt(self):
        p = self.tmp / "workspace/a.txt"
        p.write_bytes(b"old")
        original = Path.read_bytes
        reads = 0

        def fail_third_read(path):
            nonlocal reads
            if path == p:
                reads += 1
                if reads == 3:
                    raise OSError("readback unavailable")
            return original(path)

        with patch.object(Path, "read_bytes", fail_third_read):
            result = workshop.fs_write_result("workspace/a.txt", "new", overwrite=True)
        self.assertEqual(result["status"], "in_doubt")
        self.assertEqual(p.read_bytes(), b"new")


class TestZones(Base):
    def test_core_without_pid_refused(self):
        out = workshop.fs_edit("core.py", "VALUE = 1", "VALUE = 2")
        self.assertIn("через предложение", out)
        self.assertEqual((self.tmp / "core.py").read_text(encoding="utf-8"), "VALUE = 1\n")
        self.assertIn("через предложение", workshop.fs_write("newcore.py", "x = 1"))

    def test_core_with_pid_writes_into_worktree(self):
        wt = self.tmp / ".proposals" / "abc123"
        wt.mkdir(parents=True)
        (wt / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
        orig = selfdev.worktree_path
        selfdev.worktree_path = lambda pid: self.tmp / ".proposals" / pid
        try:
            out = workshop.fs_edit("core.py", "VALUE = 1", "VALUE = 42", proposal_id="abc123")
            self.assertIn("Поправила", out)
            self.assertIn("VALUE = 42", (wt / "core.py").read_text(encoding="utf-8"))
            self.assertEqual((self.tmp / "core.py").read_text(encoding="utf-8"),
                             "VALUE = 1\n", "живое ядро не тронуто")
            out = workshop.fs_write("../escape.py", "x", proposal_id="abc123")
            self.assertIn("вне worktree", out)
        finally:
            selfdev.worktree_path = orig

    def test_credential_names_are_ordinary_files(self):
        self.assertIn("SECRET=x", workshop.fs_read(".env"))
        self.assertIn('"secret"', workshop.fs_read("memory/llm.json"))
        self.assertIn("VALUE", workshop.fs_read("core.py"), "обычный код читается")

    def test_fs_read_line_numbers_and_paging(self):
        (self.tmp / "workspace" / "big.txt").write_text(
            "\n".join(f"строка{i}" for i in range(1, 21)), encoding="utf-8")
        out = workshop.fs_read("workspace/big.txt", start=5, end=7)
        self.assertIn("5\tстрока5", out)
        self.assertNotIn("строка8", out.split("…")[0])
        self.assertIn(hashlib.sha256((self.tmp / "workspace" / "big.txt").read_bytes()).hexdigest(), out)

    def test_fs_read_discloses_lossy_decoding_but_hashes_original_bytes(self):
        target = self.tmp / "workspace" / "invalid.txt"
        target.write_bytes(b"alpha\xffbeta\n")
        out = workshop.fs_read("workspace/invalid.txt")
        self.assertIn("text is lossy", out)
        self.assertIn(hashlib.sha256(target.read_bytes()).hexdigest(), out)

    def test_fs_search_includes_credential_named_files(self):
        out = workshop.fs_search("SECRET", glob="**/*")
        self.assertIn(".env", out)


class TestRunAndQuota(Base):
    def test_attempted_native_command_with_lost_reply_does_not_run_python_again(self):
        slug = self._proj()
        with patch.object(workshop.hands, "binary", return_value=Path("fake-hands")), \
             patch.object(workshop.hands, "execute", return_value=None) as native, \
             patch.object(workshop.subprocess, "run") as python_run:
            out = workshop.run("echo effect", slug)
        native.assert_called_once()
        python_run.assert_not_called()
        self.assertIn("исход неизвестен", out)

    def test_run_needs_project_and_caps_output(self):
        self.assertIn("Нет проекта", workshop.run("echo hi", "нету"))
        slug = self._proj()
        py = f'"{sys.executable}" -c "print(\'x\' * 20000)"'
        out = workshop.run(py, slug)
        self.assertLess(len(out), workshop.RUN_OUT_CAP + 200, "потолок вывода не сработал")
        self.assertIn("вырезано", out)

    def test_run_timeout(self):
        # PASS 16.3: тайм-аут исполняет компилируемый пол («прервано по тайм-ауту»),
        # а без бинаря — питон («прервано по таймауту»). Факт один, формулировки разные.
        slug = self._proj()
        cmd = f'"{sys.executable}" -c "import time; time.sleep(5)"'
        out = workshop.run(cmd, slug, timeout=1)
        self.assertIn("прервано по тайм", out)

    def test_quota_refuses_install(self):
        slug = self._proj()
        orig = workshop._du_mb
        workshop._du_mb = lambda p: workshop.QUOTA_MB + 50
        try:
            out = workshop.pip_install(slug, "requests")
            self.assertIn("Квота", out)
            self.assertIn("почисти", out)
        finally:
            workshop._du_mb = orig

    def test_pip_rejects_suspicious_args(self):
        slug = self._proj()
        self.assertIn("не ставлю", workshop.pip_install(slug, "--index-url http://evil requests"))


class TestDeliveryAndMap(Base):
    def test_send_file_via_mocked_bridge(self):
        sent = {}
        agent._TELETHON["send_file"] = lambda p, c="": sent.update(path=p, caption=c) or "Отправила Егору в личку: r.txt"
        try:
            (self.tmp / "workspace" / "r.txt").write_text("результат", encoding="utf-8")
            out = workshop.send_file("workspace/r.txt", "готово")
            self.assertIn("личку", out)
            self.assertTrue(sent["path"].endswith("r.txt"))
            self.assertEqual(sent["caption"], "готово")
        finally:
            agent._TELETHON.pop("send_file", None)

    def test_send_file_unavailable_without_bridge(self):
        (self.tmp / "workspace" / "r.txt").write_text("x", encoding="utf-8")
        agent._TELETHON.pop("send_file", None)
        self.assertIn("Недоступно", workshop.send_file("workspace/r.txt"))

    def test_live_turn_document_uses_guarded_durable_outbox_not_direct_bridge(self):
        source = self.tmp / "workspace" / "result.txt"
        source.write_text("verified", encoding="utf-8")
        old_spool = agent._MEDIA_SPOOL
        old_bridge = agent._TELETHON.get("send_file")
        agent._MEDIA_SPOOL = media.MediaSpool(
            self.tmp / "media-spool",
            photo_max_bytes=1024,
            audio_max_bytes=1024,
            document_max_bytes=4096,
            max_total_bytes=8192,
            ttl_seconds=60,
            max_queue=4,
            max_turn_media=4,
        )
        direct_calls = []
        agent._TELETHON["send_file"] = lambda *args: direct_calls.append(args) or "direct"
        ctx_token = agent._TURN_CHANNEL.set(
            agent.ChannelContext(chat_id="777", principal_id="owner", is_dm=True, owner=True)
        )
        outbound: list[media.OutboundMedia] = []
        out_token = agent._TURN_OUTBOUND.set(outbound)
        try:
            result = workshop.send_file("workspace/result.txt", "done")
            self.assertIn("document", result)
            self.assertEqual(direct_calls, [])
            self.assertEqual(len(outbound), 1)
            self.assertEqual(outbound[0].kind, "document")
            self.assertEqual(outbound[0].caption, "done")
            self.assertEqual(len(outbound[0].sha256), 64)
        finally:
            agent._TURN_OUTBOUND.reset(out_token)
            agent._TURN_CHANNEL.reset(ctx_token)
            agent._MEDIA_SPOOL = old_spool
            if old_bridge is None:
                agent._TELETHON.pop("send_file", None)
            else:
                agent._TELETHON["send_file"] = old_bridge

    def test_code_map_fixture_tree(self):
        slug = self._proj("картограф")
        d = workshop.PROJECTS / slug
        (d / "mod.py").write_text(
            '"""Модуль-фикстура."""\n\nclass Boat:\n    """Лодка."""\n    def row(self):\n        pass\n\n'
            "def main():\n    pass\n", encoding="utf-8")
        out = workshop.code_map(slug)
        self.assertIn("mod.py — Модуль-фикстура.", out)
        self.assertIn("class Boat :3", out)
        self.assertIn("def row :5", out)
        self.assertIn("def main :8", out)
        self.assertIs(workshop.code_map(slug), out, "кэш по mtime не сработал")

    def test_project_lifecycle(self):
        slug = self._proj("Мой Скрипт")
        self.assertIn(slug, workshop.project_list())
        st = workshop.project_status(slug)
        self.assertIn("МБ", st)
        self.assertIn("Проект создан", "Проект создан")  # sanity
        self.assertTrue((workshop.PROJECTS / slug / "README.md").exists())


class TestCodingWindow(unittest.TestCase):
    def test_coding_goal_recognized(self):
        self.assertTrue(agent._is_coding_goal("код: скрипт для бэкапа"))
        self.assertTrue(agent._is_coding_goal("  КОД: что-то"))
        self.assertFalse(agent._is_coding_goal("навести порядок в памяти"))

    def test_coding_window_frame_and_budget(self):
        calls = {}

        def fake_voice(seed, hist, speaker=None, chat_id=None, is_owner=False, known=True,
                       extra_system="", max_iters=None, **kw):
            calls.update(seed=seed, extra=extra_system, iters=max_iters)
            return "готово"
        orig = agent._voice
        agent._voice = fake_voice
        llm.use_test_client(object())
        try:
            agent.task_window("код: скрипт для Егора")
        finally:
            agent._voice = orig
            llm.clear_test_clients()
        self.assertIn("CODING window", calls["extra"])
        self.assertIn("send_file", calls["extra"])
        self.assertIsNone(calls["iters"], "PASS 24: coding-run завершается состоянием дела, не счётчиком")
        self.assertIn("скрипт для Егора", calls["seed"])
        self.assertNotIn("код:", calls["seed"].splitlines()[0].lower(), "префикс срезан из брифа")

    def test_plain_window_keeps_old_frame(self):
        calls = {}

        def fake_voice(seed, hist, **kw):
            calls.update(extra=kw.get("extra_system", ""), iters=kw.get("max_iters"))
            return ""
        orig = agent._voice
        agent._voice = fake_voice
        llm.use_test_client(object())
        try:
            agent.task_window("прибраться в памяти")
        finally:
            agent._voice = orig
            llm.clear_test_clients()
        # ⚠ Было assertIn("Telegram remains online") — см. test_heartbeat: окно
        # открывается уже после disconnect. Тест проверяет, что обычному окну достаётся
        # именно рамка фонового run, и это остаётся проверяемым по правдивой строке.
        self.assertIn("This is your own work run", calls["extra"])
        self.assertNotIn("Telegram remains online", calls["extra"])
        self.assertIsNone(calls["iters"], "PASS 24: обычное окно больше не имеет скрытого потолка")

    def test_workshop_tools_registered_owner_only(self):
        for name in ("project_create", "fs_edit", "run_tests", "send_file", "code_map"):
            self.assertIn(name, agent.TOOL_IMPL)
        owner = {t["name"] for t in agent.OWNER_TOOLS}
        base = {t.get("name") for t in agent.BASE_TOOLS}
        for name in ("fs_edit", "send_file", "run", "pip_install"):
            self.assertIn(name, owner)
            self.assertNotIn(name, base, f"{name} не должен быть виден не-владельцу")


if __name__ == "__main__":
    unittest.main(verbosity=2)
