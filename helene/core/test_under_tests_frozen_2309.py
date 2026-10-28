"""Живой процесс, подтянувший `unittest`, не становится тестом.

21.09 в живой контейнер поставили torch. Прогрев whisper на буте тянет faster_whisper →
ctranslate2 → torch, а `torch/utils/_config_module.py` делает `import unittest`. Гейт
`_under_tests()` смотрел в `sys.modules` на каждом вызове — и с 21.09 19:19 прод считал
себя тестом: молча перестали писаться события жизни (`_buf_push`, `_persist_sent_reply`),
архив групп и свёртка. Лента ЛС замёрзла, кадр подавал старое сообщение как текущее,
бут пересобирал буферы из замёрзшего слоя, и boot-sweep воскрешал отвеченные ЛС:
за сутки шесть лишних ответов Егору и восемь — torvn77.

Вердикт теперь снимается при импорте, когда torch ещё не загружен.

Запуск:  python praxis_test.py test_under_tests_frozen_2309 -v
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

import agent
import mtproto_runner as runner


def _without_test_env():
    env = {k: v for k, v in os.environ.items()
           if k not in ("PRAXIS_TEST", "PYTEST_CURRENT_TEST")}
    return mock.patch.dict(os.environ, env, clear=True)


class ProductionVerdictSurvivesLateUnittestImportTests(unittest.TestCase):
    def test_runner_verdict_ignores_late_unittest_in_sys_modules(self):
        self.assertIn("unittest", sys.modules)
        with _without_test_env(), \
                mock.patch.object(runner, "_UNDER_TESTS_AT_IMPORT", False):
            self.assertFalse(runner._under_tests())

    def test_runner_env_still_marks_a_test(self):
        with _without_test_env(), \
                mock.patch.object(runner, "_UNDER_TESTS_AT_IMPORT", False), \
                mock.patch.dict(os.environ, {"PRAXIS_TEST": "1"}):
            self.assertTrue(runner._under_tests())

    def test_agent_run_spine_is_not_sent_to_tmp_after_late_unittest(self):
        self.assertIn("unittest", sys.modules)
        with _without_test_env(), \
                mock.patch.object(agent, "_TEST_RUNTIME_AT_IMPORT", False):
            self.assertFalse(agent._test_runtime())

    def test_buf_push_records_life_in_production(self):
        """Сам симптом: при проде `_buf_push` пишет событие жизни, что бы ни лежало в sys.modules."""
        from collections import defaultdict, deque
        with _without_test_env(), \
                mock.patch.object(runner, "_UNDER_TESTS_AT_IMPORT", False), \
                mock.patch.object(runner, "_buf", defaultdict(lambda: deque(maxlen=5))), \
                mock.patch.object(runner, "_buffer_message_ids",
                                  defaultdict(lambda: deque(maxlen=5))), \
                mock.patch.object(runner, "_buf_dirty", set()), \
                mock.patch.object(runner.bufstore, "meta_update"), \
                mock.patch.object(runner, "_record_life_message") as record:
            runner._buf_push("7007", "torvn77: Может это у тебя глюк?",
                             author="torvn77", is_dm=True, source_id=4566)
        record.assert_called_once()
        self.assertEqual(record.call_args.kwargs["dedupe_key"], "telegram:7007:4566:in")


if __name__ == "__main__":
    unittest.main()
