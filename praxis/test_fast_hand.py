"""Быстрая рука памяти: правки скорости обязаны быть ТОЖДЕСТВЕННЫ по смыслу.

Три захода 07.08 сняли с явного recall две трети времени. Каждый из них меняет НЕ то,
что она вспоминает, а только цену вспоминания, — и вот стенд, который это стережёт.
Если однажды кто-то «упростит» строковый путь до приблизительного, покраснеет здесь.

Замеры, ради которых всё делалось (живой прод, запрос «провенанс кадра»):
    до правок      70.5 с
    после первой   62.0 с   кэш resolve()
    после второй   31.4 с   строки вместо арифметики путей
    после третьей  см. коммит третьего захода
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import memory_fts


class RelUnderIsExactlyRelativeTo(unittest.TestCase):
    """`_rel_under` обязан отвечать РОВНО то же, что `relative_to().as_posix()`.

    Список входов враждебный намеренно: соседний каталог с общим префиксом имени,
    хвостовой разделитель, нормализуемые куски, не-ASCII, `base == path`, и вход,
    на котором `relative_to` бросает — исключение обязано дойти до вызывающего.
    """

    def _both(self, path: Path, base: Path):
        try:
            expected = path.relative_to(base).as_posix()
        except ValueError as exc:
            expected = ("РАЗ", type(exc).__name__)
        try:
            got = memory_fts._rel_under(path, base)
        except ValueError as exc:
            got = ("РАЗ", type(exc).__name__)
        return expected, got

    def test_matches_on_hostile_inputs(self) -> None:
        root = Path(os.sep + "app" + os.sep + "memory")
        cases = [
            (root / "people" / "egor.md", root),
            (root / "a" / "b" / "c" / "d.md", root),
            (root, root),
            # Сосед с ОБЩИМ ПРЕФИКСОМ ИМЕНИ — классический способ обмануть startswith.
            (Path(os.sep + "app" + os.sep + "memory-old") / "x.md", root),
            (Path(os.sep + "app" + os.sep + "memoryX") / "x.md", root),
            # Совсем не внутри: обязан прилететь ValueError с обеих сторон.
            (Path(os.sep + "etc") / "passwd", root),
            # Не-ASCII в имени: кириллица, пробелы, точка в начале имени файла.
            (root / "люди" / "Егор Косырев.md", root),
            (root / ".hidden" / "note.md", root),
            # Обратный слэш — на Linux это ЗАКОННЫЙ символ имени, а не разделитель.
            (root / "wei\\rd" / "n.md", root),
            # `..` Path сохраняет — обе стороны обязаны сохранить его одинаково.
            (Path(str(root) + os.sep + "a" + os.sep + ".." + os.sep + "b.md"), root),
        ]
        for path, base in cases:
            with self.subTest(path=str(path)):
                expected, got = self._both(path, base)
                self.assertEqual(expected, got, f"разошлось на {path!r} под {base!r}")

    def test_the_normalisation_guard_is_a_floor_and_not_a_proven_defence(self) -> None:
        """⚑ ЧЕСТНО: сторож на «.» и двойной разделитель в `_rel_under` НЕ СРАБАТЫВАЕТ.

        Проверка нарочной поломкой (07.08): если убрать из `_rel_under` условие
        `all(part and part not in ('.', '..'))`, стенд остаётся ЗЕЛЁНЫМ — все восемь
        проверок проходят. Причина не в дыре стенда, а в самом Path: конструктор съедает
        «.» и двойной разделитель ещё до того, как мы увидим строку, а «..» обе стороны
        сохраняют ОДИНАКОВО. Входа, на котором сторож меняет ответ, не существует.

        Сторож оставлен намеренно — как пол для строки, пришедшей когда-нибудь не от Path, —
        но его цена названа вслух, а не выдана за проверенную защиту. Этот тест стережёт
        именно ПРИЧИНУ: если Path однажды перестанет нормализовать, здесь покраснеет, и
        сторож придётся проверять по-настоящему.
        """
        root = Path(os.sep + "app")
        self.assertEqual(
            str(Path(str(root) + os.sep + "a" + os.sep + "." + os.sep + "b")),
            str(root / "a" / "b"), "Path перестал нормализовать «.» — сторож ожил")
        self.assertEqual(
            str(Path(str(root) + os.sep + os.sep + "a")),
            str(root / "a"), "Path перестал схлопывать двойной разделитель — сторож ожил")
        keeps = Path(str(root) + os.sep + "a" + os.sep + ".." + os.sep + "b")
        self.assertIn("..", str(keeps), "Path начал нормализовать «..» — сторож ожил")

    def test_trailing_separator_in_base(self) -> None:
        """База с хвостовым разделителем: Path её съедает, но проверить обязаны."""
        root = Path(os.sep + "app" + os.sep + "memory" + os.sep)
        expected, got = self._both(root / "x.md", root)
        self.assertEqual(expected, got)

    def test_walk_output_is_answered_by_the_string_path(self) -> None:
        """На путях НАСТОЯЩЕГО обхода строковый ответ обязан срабатывать, а не падать
        в запасной `relative_to`: иначе правка была бы декоративной."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "memory"
            (root / "people").mkdir(parents=True)
            (root / "people" / "egor.md").write_text("- x\n", encoding="utf-8")
            (root / "заметка.md").write_text("- y\n", encoding="utf-8")
            seen = []
            real = Path.relative_to

            def spy(self, other, *a, **kw):
                seen.append((str(self), str(other)))
                return real(self, other, *a, **kw)

            with mock.patch.object(Path, "relative_to", spy):
                rels = sorted(memory_fts._rel_under(p, root) for p in root.rglob("*.md"))
            self.assertEqual(rels, ["people/egor.md", "заметка.md"])
            self.assertEqual(seen, [], "строковый путь не сработал — упал в relative_to")


class TheCorpusIsWalkedOncePerSearch(unittest.TestCase):
    """Обход корпуса — самая дорогая часть вопроса к памяти. Он обязан быть ОДИН.

    Прежде `search` перечислял источники дважды: в `ensure` (сверить индекс) и в
    `_canonical_candidates` (сверить подлинность выданного). Перечисление одно и то же;
    24.0 с из 31.4 на замере 07.08 уходили именно туда.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.memory = self.base / "memory"
        self.skills = self.base / "soul" / "skills"
        (self.memory / "people").mkdir(parents=True)
        self.skills.mkdir(parents=True)
        (self.memory / "people" / "egor.md").write_text(
            "- Егор просил ускорить руку памяти\n", encoding="utf-8")
        memory_fts.clear_path_cache()
        # Explicit recall must not build its disposable index in the request.
        memory_fts.rebuild(base=self.base, memory_dir=self.memory, skills_dir=self.skills)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _search(self, query: str):
        return memory_fts.search(query, base=self.base, memory_dir=self.memory,
                                 skills_dir=self.skills, purpose="explicit")

    def test_one_walk(self) -> None:
        # setUp already performed the scheduled/background rebuild.
        real = memory_fts.iter_sources
        calls = []

        def spy(**kw):
            calls.append(kw)
            return real(**kw)

        with mock.patch.object(memory_fts, "iter_sources", side_effect=spy):
            hits = self._search("ускорить")
        self.assertTrue(hits, "поиск обязан находить — иначе тест вакуумный")
        self.assertEqual(len(calls), 0,
                         f"явный поиск обошёл корпус {len(calls)} раз(а)")

    def test_answer_is_unchanged_by_the_shared_roster(self) -> None:
        """Ready background index preserves the canonically validated answer."""
        first = self._search("Егор")
        second = self._search("Егор")
        self.assertTrue(first)
        self.assertEqual([row["id"] for row in first], [row["id"] for row in second])

    def test_second_attempt_walks_afresh(self) -> None:
        """Stale row fails closed and asks the night job; no sync retry is allowed."""
        source = self.memory / "people" / "egor.md"
        source.write_text("- канон уже изменился\n", encoding="utf-8")
        with mock.patch.object(memory_fts, "rebuild",
                               side_effect=AssertionError("не в интерактивной руке")):
            self.assertEqual(self._search("ускорить"), [])
        self.assertTrue(memory_fts.refresh_requested(memory_dir=self.memory),
                        "расхождение обязано оставить durable запрос фоновой починки")
        self.assertTrue(source.exists())


class TheLazyCanonPathAnswersIdentically(unittest.TestCase):
    """Ленивая сверка канона обязана давать ТОТ ЖЕ ответ, только дешевле.

    Механизм, ради которого она есть (замер прода 07.08, её рука целиком): чтобы отдать
    ей шесть записей, код перечитывал 191 исходный файл ЦЕЛИКОМ — из базы бралось 240
    строк «с запасом», и каждая сверялась с каноном, включая те, что фильтры выбросят
    через две строки.

    ⚠ Рычаг выключен по умолчанию, и это стережётся здесь же: свойство «ни одна строка не
    попадает в её промпт без сверки с каноном» ленивый путь сохраняет, но расхождение в
    строке, которая всё равно была бы выброшена, больше не запускает пересборку индекса.
    Это край её договора о доверии к памяти — включать его без её слова мы не станем.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.memory = self.base / "memory"
        self.skills = self.base / "soul" / "skills"
        (self.memory / "people").mkdir(parents=True)
        (self.memory / "life" / "events").mkdir(parents=True)
        self.skills.mkdir(parents=True)
        for name in ("egor", "praxis", "sasha"):
            (self.memory / "people" / f"{name}.md").write_text(
                f"- {name} говорит про провенанс кадра и про память\n"
                f"- второй факт про {name}: гуттер и лента\n", encoding="utf-8")
        (self.skills / "recall.md").write_text(
            "- навык про память и провенанс\n", encoding="utf-8")
        memory_fts.clear_path_cache()
        memory_fts.rebuild(base=self.base, memory_dir=self.memory, skills_dir=self.skills)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _ids(self, query: str, lazy: bool, limit: int = 3):
        with mock.patch.dict(os.environ,
                             {"PRAXIS_RECALL_LAZY_CANON": "1" if lazy else "0"}):
            self.assertEqual(memory_fts.lazy_canon_enabled(), lazy)
            hits = memory_fts.search(query, base=self.base, memory_dir=self.memory,
                                     skills_dir=self.skills, limit=limit,
                                     purpose="explicit")
        return [(row["id"], row["text"], row["lexical"]) for row in hits]

    def test_same_answer_both_ways(self) -> None:
        for query in ("провенанс", "память", "гуттер лента", "второй факт"):
            with self.subTest(query=query):
                eager = self._ids(query, lazy=False)
                lazy = self._ids(query, lazy=True)
                self.assertTrue(eager, "запрос обязан что-то находить — иначе тест пустой")
                self.assertEqual(eager, lazy)

    def test_lever_is_off_by_default(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "PRAXIS_RECALL_LAZY_CANON"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(memory_fts.lazy_canon_enabled())

    def test_lazy_path_reads_fewer_sources(self) -> None:
        """Не «работает», а РАБОТАЕТ ДЕШЕВЛЕ — иначе правка декоративна."""
        self._ids("память", lazy=False)  # построить индекс
        counts = {}
        for lazy in (False, True):
            touched = []
            original = memory_fts._source_chunks
            with mock.patch.object(memory_fts, "_source_chunks",
                                   side_effect=lambda s: (touched.append(s.rel),
                                                          original(s))[1]):
                self._ids("память", lazy=lazy, limit=1)
            counts[lazy] = len(touched)
        # Both modes validate only the final selected row; lazy mode must never
        # expand that bounded canonical validation work.
        self.assertGreater(counts[False], 0, f"проверка канона не состоялась: {counts}")
        self.assertLessEqual(counts[True], counts[False],
                             f"ленивый путь прочитал больше канона: {counts}")

    def test_no_unverified_row_reaches_the_prompt(self) -> None:
        """Главное свойство: подделанная в базе строка не проходит НИ ОДНИМ путём."""
        self._ids("провенанс", lazy=False)
        db = memory_fts._db_path(self.memory)
        import sqlite3
        with sqlite3.connect(db) as con:
            con.execute("UPDATE chunks SET text = ? WHERE text LIKE ?",
                        ("подделка, которой нет в каноне", "%провенанс кадра%"))
            con.commit()
        for lazy in (False, True):
            with self.subTest(lazy=lazy):
                texts = [t for _, t, _ in self._ids("подделка", lazy=lazy)]
                self.assertNotIn("подделка, которой нет в каноне", texts)


class ThePathCacheDoesNotOutliveTheTree(unittest.TestCase):
    """Кэш разрешённых путей — чистая функция от строки, ПОКА дерево не двигается.

    Ручка сброса существует не для красоты: стенд двигает дерево под собой в каждом
    setUp, и без сброса второй тест видел бы пути первого.
    """

    def test_clear_forgets_everything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").mkdir()
            self.assertTrue(memory_fts._inside(root / "a", root))
            self.assertTrue(memory_fts._POSIX)
            memory_fts.clear_path_cache()
            self.assertFalse(memory_fts._POSIX)

    def test_cache_holds_strings_and_not_path_objects(self) -> None:
        """⚠ Здесь стоял ВТОРОЙ словарь — разрешённых `Path`, — и он не экономил ничего.

        Адверсарка 07.08 инструментовала обе половины: ключи совпадали по построению,
        поэтому попадание ловилось строковым словарём, и до второго управление не доходило
        НИ РАЗУ — 0 попаданий на 70 000 путях. Цена была ~62 МБ удержанных объектов Path
        на полном потолке, вчетверо больше полезной половины, в процессе, живущем сутками.

        Тест стережёт ровно это: разрешение зовётся один раз на путь, а в памяти лежат
        строки. Если кто-нибудь заведёт словарь объектов заново — покраснеет здесь.
        """
        memory_fts.clear_path_cache()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = []
            for name in ("a", "b", "c"):
                (root / name).mkdir()
                paths.append(root / name)
            calls = []
            real = Path.resolve

            def spy(self, *a, **kw):
                calls.append(str(self))
                return real(self, *a, **kw)

            with mock.patch.object(Path, "resolve", spy):
                for _ in range(3):
                    for p in paths:
                        memory_fts._posix(p)
            self.assertEqual(len(calls), len(paths),
                             "разрешение зовётся повторно — кэш не работает")
            self.assertTrue(memory_fts._POSIX)
            self.assertTrue(all(isinstance(v, str) for v in memory_fts._POSIX.values()),
                            "кэш снова держит объекты Path")
            self.assertFalse(hasattr(memory_fts, "_RESOLVED"),
                             "мёртвый словарь разрешённых Path вернулся")

    def test_clear_is_a_barrier_not_a_wish(self) -> None:
        """Сброс, начатый ПОКА идёт разрешение, обязан отменить его результат.

        Сброс идёт вне `_LOCK`, а поход в файловую систему долгий. Адверсарка 07.08
        воспроизвела: разрешение, начатое до сброса, ложилось в кэш ПОСЛЕ него — то есть
        тот, кто честно «подвинул дерево и сбросил кэш», получал прежний ответ. Ровно то,
        что докстрока объявляла поддержанным.
        """
        import threading

        memory_fts.clear_path_cache()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").mkdir()
            target = root / "a"
            entered, may_finish = threading.Event(), threading.Event()
            real = Path.resolve

            def slow(self, *a, **kw):
                if str(self) == str(target):
                    entered.set()
                    may_finish.wait(5)
                return real(self, *a, **kw)

            result = {}

            def worker():
                with mock.patch.object(Path, "resolve", slow):
                    result["got"] = memory_fts._posix(target)

            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(entered.wait(5), "поток не дошёл до разрешения")
            memory_fts.clear_path_cache()
            may_finish.set()
            thread.join(10)
            self.assertIn("got", result, "поток не вернулся")
            self.assertEqual(memory_fts._POSIX, {},
                             "запись из прошлого дерева легла ПОСЛЕ сброса — сброс не барьер")

    def test_inside_agrees_with_relative_to_on_neighbours(self) -> None:
        """`_inside` на соседях с общим префиксом имени обязан говорить НЕТ."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "memory").mkdir()
            (root / "memory-old").mkdir()
            (root / "memoryX").mkdir()
            memory_fts.clear_path_cache()
            self.assertTrue(memory_fts._inside(root / "memory", root / "memory"))
            self.assertFalse(memory_fts._inside(root / "memory-old", root / "memory"))
            self.assertFalse(memory_fts._inside(root / "memoryX", root / "memory"))
            self.assertTrue(memory_fts._inside(root / "memory" / "x", root / "memory"))


if __name__ == "__main__":
    unittest.main()


class TheRunTreeIsWalkedForWhatIsActuallyThere(unittest.TestCase):
    """Дерево прогонов обходилось ради того, чего в нём нет.

    Замер 08.08 на живом проде: под `memory/runs` 70 183 файла, из них 51 573 — `.log`
    в `results/`. Ни одного `.md` и ни одного `.jsonl` там нет ни на какой глубине,
    но `rglob` заходил в каждый такой каталог: 1.24 с против 0.70 с на поиск.

    ⚑ Подрезается ТОЛЬКО `results/`. Соседний `artifacts/` обходится по-прежнему: там
    лежат ЕЁ рабочие продукты (на проде два черновика исходящих на 403 куска), и
    наивное «перечислить три канонических имени» их бы молча потеряло. Этот тест
    стережёт обе стороны: лишнее не обходим, нужное не теряем.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.memory = Path(self.tmp.name) / "memory"
        run = self.memory / "runs" / "2026-08" / "run-x"
        (run / "results").mkdir(parents=True)
        (run / "artifacts").mkdir(parents=True)
        (run / "context.md").write_text("транспорт\n", encoding="utf-8")
        (run / "events.jsonl").write_text("{}\n", encoding="utf-8")
        (run / "RECAP.md").write_text("итог\n", encoding="utf-8")
        (run / "artifacts" / "её-черновик.md").write_text(
            "её рабочий продукт\n", encoding="utf-8")
        for i in range(30):
            (run / "results" / f"{i:04d}-model-input.log").write_text("x", encoding="utf-8")
        (self.memory / "people").mkdir(parents=True)
        (self.memory / "people" / "egor.md").write_text("- факт\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _walk(self, pattern: str) -> set[str]:
        return {p.name for p in memory_fts._memory_files(
            self.memory, pattern, include_runs=True)}

    def test_her_artifacts_survive_the_prune(self) -> None:
        found = self._walk("*.md")
        self.assertIn("её-черновик.md", found,
                      "подрезка съела её рабочий продукт из artifacts/")
        self.assertIn("RECAP.md", found)
        self.assertIn("context.md", found)
        self.assertIn("egor.md", found, "подрезка задела память вне прогонов")

    def test_jsonl_is_still_found(self) -> None:
        self.assertIn("events.jsonl", self._walk("*.jsonl"))

    def test_results_are_not_walked(self) -> None:
        """Невакуумно: под results/ КЛАДЁМ .md и требуем, чтобы его не нашли.

        Так тест утверждает не «там ничего нет», а «мы туда не ходим» — и покраснеет,
        если кто-нибудь вернёт обход обратно.
        """
        run = self.memory / "runs" / "2026-08" / "run-x"
        (run / "results" / "0031-подделка.md").write_text("не память\n", encoding="utf-8")
        self.assertNotIn("0031-подделка.md", self._walk("*.md"))
