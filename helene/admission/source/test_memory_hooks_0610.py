"""06.10: confidence в маркерах фактов + открытые note-вопросы в THREADS-карте.

Случаи: hook предпочитает observed-claim; маркер с |inferred парсится; старый
маркер без confidence работает; открытый вопрос появляется в THREADS; formation
пишет confidence в маркер досье; append_fact не роняет «|» из source_ref.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _claim_id(subject: str, statement: str) -> str:
    key = hashlib.sha256(
        f"{subject.casefold()}\0{statement.casefold()}".encode()).hexdigest()[:16]
    return f"clm-{key}"


class MemoryHooksBase(unittest.TestCase):
    """Каталожная песочница: base + memory, валидные claim-файлы по рецептуре."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.memory = self.base / "memory"
        (self.memory / "people").mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {"PRAXIS_BASE": str(self.base),
                                                "PRAXIS_OWNER_ID": "101"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _seed_event(self, event_id: str) -> None:
        events = self.memory / "life" / "events" / "2026-07-14.jsonl"
        events.parent.mkdir(parents=True, exist_ok=True)
        events.write_text(json.dumps({
            "schema": "praxis.life.event.v1", "id": event_id,
            "ts": "2026-07-14T12:00:00.000Z",
            "kind": "conversation_message", "stream": "7", "chat_id": "7",
            "actor": "Owner", "direction": "in", "text": "primary evidence",
            "source": "telegram", "source_id": "1", "salience": 2,
            "refs": [], "meta": {},
        }) + "\n", encoding="utf-8")

    def _write_claim(self, subject: str, statement: str, confidence: str) -> str:
        import memory_provenance

        cid = _claim_id(subject, statement)
        event_id = "evt-20260714T120000000000Z-00000000"
        self._seed_event(event_id)
        path = self.memory / "life" / "claims" / f"{cid}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "schema": memory_provenance.CLAIM_SCHEMA,
            "id": cid, "subject": subject, "kind": "person",
            "status": "supported", "confidence": confidence,
            "salience": 2, "visibility": "public",
            "evidence_ids": [event_id], "contradicts": [],
            "updated_at": "2026-07-14T12:00:00.000Z",
            "last_run": "rr-20260714T120000000000Z-00000000",
            "path": path.relative_to(self.base).as_posix(),
        }
        path.write_text(
            f"<!-- praxis-claim: {json.dumps(meta, separators=(',', ':'))} -->\n"
            f"# Claim {cid}\n\n## Утверждение\n**{subject}** — {statement}\n\n"
            f"## Статус\n- status: `supported`\n- confidence: `{confidence}`\n"
            "- salience: `2`\n- visibility: `public`\n"
            f"- evidence: {event_id}\n- contradicts: нет\n\n## Revisions\n"
            f"- 2026-07-14T12:00:00.000Z · {meta['last_run']} · **supported** · test\n",
            encoding="utf-8",
        )
        return cid


class TestHookConfidence(MemoryHooksBase):
    def test_hook_prefers_observed_claim_over_earlier_inferred(self):
        """Наблюдение сильнее вывода, даже если вывод стоит в файле раньше."""
        import memory_catalog

        inferred_id = self._write_claim("Человек", "давний вывод", "inferred")
        observed_id = self._write_claim("Человек", "прямое наблюдение", "observed")
        (self.memory / "people" / "chelovek.md").write_text(
            "# Человек\n\n## Факты\n"
            f"- [public] (s2) давний вывод _(2026-07-14)_ [source:{inferred_id}|inferred]\n"
            f"- [public] (s2) прямое наблюдение _(2026-07-15)_ [source:{observed_id}|observed]\n",
            encoding="utf-8")
        hook = memory_catalog._derive_person_hook(
            self.memory / "people" / "chelovek.md")
        self.assertIn("прямое наблюдение", hook)
        self.assertNotIn("давний вывод", hook)

    def test_hook_stability_same_confidence_keeps_file_order(self):
        """Из равных по confidence — первый по порядку файла (стабильность)."""
        import memory_catalog

        first_id = self._write_claim("Человек", "первый вывод", "inferred")
        second_id = self._write_claim("Человек", "второй вывод", "inferred")
        (self.memory / "people" / "chelovek.md").write_text(
            "# Человек\n\n## Факты\n"
            f"- [public] (s2) первый вывод _(2026-07-14)_ [source:{first_id}|inferred]\n"
            f"- [public] (s2) второй вывод _(2026-07-15)_ [source:{second_id}|inferred]\n",
            encoding="utf-8")
        hook = memory_catalog._derive_person_hook(
            self.memory / "people" / "chelovek.md")
        self.assertIn("первый вывод", hook)
        self.assertNotIn("второй вывод", hook)

    def test_marker_with_confidence_suffix_parses(self):
        """Маркер `[source:clm-…|inferred]` извлекает clm-id и проходит проверку claim."""
        import memory_catalog

        cid = self._write_claim("Человек", "вывод по маркеру", "inferred")
        (self.memory / "people" / "chelovek.md").write_text(
            "# Человек\n\n## Факты\n"
            f"- [public] (s2) вывод по маркеру _(2026-07-14)_ [source:{cid}|inferred]\n",
            encoding="utf-8")
        hook = memory_catalog._derive_person_hook(
            self.memory / "people" / "chelovek.md")
        self.assertIn("вывод по маркеру", hook)

    def test_old_marker_without_confidence_still_works(self):
        """Старый маркер без `|confidence` (до 06.10) читается по-прежнему."""
        import memory_catalog

        cid = self._write_claim("Человек", "старый факт", "observed")
        (self.memory / "people" / "chelovek.md").write_text(
            "# Человек\n\n## Факты\n"
            f"- [public] (s2) старый факт _(2026-07-14)_ [source:{cid}]\n",
            encoding="utf-8")
        hook = memory_catalog._derive_person_hook(
            self.memory / "people" / "chelovek.md")
        self.assertIn("старый факт", hook)

    def test_hook_stays_single_line_without_confidence_tail(self):
        """Hook — grep-указатель: одна строка ≤140; confidence не дописывается
        хвостом через « — » (маркер источника в строке — его исходная часть)."""
        import re as _re
        import memory_catalog

        cid = self._write_claim("Человек", "факт одной строкой", "observed")
        (self.memory / "people" / "chelovek.md").write_text(
            "# Человек\n\n## Факты\n"
            f"- [public] (s2) факт одной строкой _(2026-07-14)_ [source:{cid}|observed]\n",
            encoding="utf-8")
        hook = memory_catalog._derive_person_hook(
            self.memory / "people" / "chelovek.md")
        self.assertEqual(hook.count("\n"), 0)
        self.assertLessEqual(len(hook), 140)
        self.assertTrue(hook.startswith("Человек — "))
        # confidence не дописан отдельным сегментом после тире (это не отчёт).
        self.assertIsNone(_re.search(r" — (observed|inferred|uncertain)$", hook))


class TestThreadsOpenQuestions(MemoryHooksBase):
    def test_open_question_appears_in_threads_map(self):
        import memory_catalog
        from authored_notes import AuthoredNoteLedger

        note = AuthoredNoteLedger(self.base).write(
            "вернуться к вопросу о происхождении памяти", kind="question")
        memory_catalog.rebuild(memory_dir=self.memory)
        text = (self.memory / "maps" / "THREADS.md").read_text(encoding="utf-8")
        self.assertIn("## Открытые вопросы (notes)", text)
        self.assertIn("вернуться к вопросу", text)
        self.assertIn("memory/notes/events.jsonl", text)
        self.assertIn(note["id"], text)

        # Закрытый вопрос с карты уходит; молчание и «нет вопросов» — разные строки.
        AuthoredNoteLedger(self.base).close(note["id"], reason="разобрано")
        memory_catalog.rebuild(memory_dir=self.memory)
        text = (self.memory / "maps" / "THREADS.md").read_text(encoding="utf-8")
        self.assertNotIn("вернуться к вопросу", text)
        self.assertIn("Открытых note-вопросов нет", text)

    def test_closed_and_non_question_notes_stay_off_the_map(self):
        import memory_catalog
        from authored_notes import AuthoredNoteLedger

        ledger = AuthoredNoteLedger(self.base)
        ledger.write("просто заметка", kind="scratch")
        question = ledger.write("живой вопрос", kind="question")
        ledger.close(question["id"], reason="закрыт")
        memory_catalog.rebuild(memory_dir=self.memory)
        text = (self.memory / "maps" / "THREADS.md").read_text(encoding="utf-8")
        self.assertNotIn("просто заметка", text)
        self.assertNotIn("живой вопрос", text)


class TestFormationMarker(unittest.TestCase):
    """formation → досье: confidence доезжает до маркера; «|» не роняется."""

    def setUp(self):
        from test_pass19 import Pass19Base
        # Pass19Base.setUp патчит людей/граф/formation на одноразовое дерево.
        Pass19Base.setUp(self)

    def tearDown(self):
        from test_pass19 import Pass19Base
        Pass19Base.tearDown(self)

    def test_apply_supported_writes_confidence_marker(self):
        import formation
        import people

        candidate = {"subject": "Егор", "kind": "person",
                     "text": "любит строгое происхождение памяти",
                     "visibility": "private", "salience": 2,
                     "confidence": "observed"}
        formation._apply_supported(candidate, "clm-0123456789abcdef")
        text = people.read_text("егор")
        self.assertIn("[source:clm-0123456789abcdef|observed]", text)

    def test_apply_supported_inferred_marker(self):
        import formation
        import people

        candidate = {"subject": "Арет", "kind": "person",
                     "text": "пишет длинные письма",
                     "visibility": "public", "salience": 1,
                     "confidence": "inferred"}
        formation._apply_supported(candidate, "clm-fedcba9876543210")
        text = people.read_text("арет")
        self.assertIn("[source:clm-fedcba9876543210|inferred]", text)

    def test_append_fact_keeps_pipe_in_source_ref(self):
        """Прежде фильтр `[^\w:.-]` молча съедал «|» — маркер терял суффикс."""
        import people

        people.append_fact("test-person", "Тестовый", "факт с маркером",
                           "public", 2, source_ref="clm-0000000000000001|observed")
        text = people.read_text("test-person")
        self.assertIn("[source:clm-0000000000000001|observed]", text)


if __name__ == "__main__":
    unittest.main()
