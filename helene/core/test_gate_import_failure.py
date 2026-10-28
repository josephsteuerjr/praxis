"""Гейт обязан назвать сломанный модуль, а не онеметь целым шардом.

⚠ ТРИГГЕР ИМЕННО СИНТАКСИЧЕСКАЯ ОШИБКА, А НЕ ПРОПАВШИЙ МОДУЛЬ. Проверено на
Python 3.12 прода: `loadTestsFromName("нет_такого_модуля")` НЕ бросает — он
возвращает suite с `unittest.loader._FailedTest`, и ветка `except` не трогается
вовсе. Бросает он на `SyntaxError` — то есть ровно тогда, когда правка сломала
файл и гейт обязан быть честным.

Что ловим. Ветка «модуль не импортировался» отдавала в `_MeasuredModuleSuite`
голый `TestCase`, а тот уезжает в `TestSuite.__init__` → `addTests`, который по
аргументу ИТЕРИРУЕТСЯ. `TypeError: object is not iterable` вылетал ДО первого
теста: шард умирал молча, здоровые модули того же шарда не исполнялись, родитель
не находил ни `Ran N tests`, ни `#module-seconds`, и весь прогон объявлялся
недействительным. Цена прошлого раза, по докстрингу самой функции, — 750 тестов.

Подпроцесс намеренно: `run_shard_here` первым делом поднимает песочницу, а делать
это внутри уже идущего прогона значит менять среду соседям по шарду.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BROKEN = "test_zz_deliberately_broken_syntax"
HEALTHY = "test_zz_deliberately_healthy"


class ABrokenModuleIsOneNamedFailureNotAMuteShard(unittest.TestCase):
    def setUp(self):
        self._made = []
        for name, body in ((BROKEN, "def f(:\n"),
                           (HEALTHY, "import unittest\n"
                                     "class T(unittest.TestCase):\n"
                                     "    def test_x(self):\n        pass\n")):
            path = ROOT / f"{name}.py"
            if path.exists():                      # чужой файл не трогаем
                self.skipTest(f"{path.name} уже существует")
            path.write_text(body, encoding="utf-8")
            self._made.append(path)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for path in self._made:
            path.unlink(missing_ok=True)

    def _run_shard(self) -> subprocess.CompletedProcess:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        env.pop("PRAXIS_BASE", None)
        return subprocess.run(
            [sys.executable, str(ROOT / "praxis_test_parallel.py"),
             "--run-shard", f"{BROKEN},{HEALTHY}"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=600, env=env,
        )

    def test_the_shard_still_speaks(self):
        out = self._run_shard()
        text = out.stdout + out.stderr
        self.assertNotIn("not iterable", text,
                         "шард упал ДО тестов: родитель увидит немоту, а не падение")
        self.assertIn("Ran 2 tests", text,
                      "родитель разбирает вывод регулярками; без 'Ran N tests' "
                      "прогон объявляется недействительным:\n" + text[-1500:])

    def test_the_healthy_neighbour_still_runs(self):
        # Соседний модуль не виноват в чужой синтаксической ошибке.
        text = self._run_shard().stdout
        self.assertIn(f"#module-seconds {HEALTHY} ", text,
                      "здоровый модуль шарда не исполнился из-за чужого импорта")

    def test_the_broken_module_is_named_and_charged_to_itself(self):
        text = self._run_shard().stdout
        self.assertIn(f"#module-seconds {BROKEN} ", text,
                      "без своей строки длительности сломанный модуль отравляет "
                      ".praxis_test_durations.json средним по шарду")
        self.assertIn(BROKEN, text, "падение обязано назвать модуль")


if __name__ == "__main__":
    unittest.main()
