"""
Находки адверсарки 25.07 по пассу «место» — каждая закреплена тестом.

29 агентов, 18 находок, 17 пережили опровержение. Ни одну из них я бы не увидел сам:
все они об одном и том же — утверждение в комментарии расходилось с тем, что делает код.
Поэтому здесь проверяется не «работает», а именно то УТВЕРЖДЕНИЕ, которое расходилось.

Запуск:  python praxis_test.py test_places_adversarial -v
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import group_context
import memory_life as ml
import telegram_routes as tr

ROOM = "-1001240718803"
FORUM = "-1004301095307"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="praxis_adv_"))
        self._gc = [(group_context, "BASE", group_context.BASE),
                    (group_context, "GROUPS_DIR", group_context.GROUPS_DIR),
                    (tr, "DIR", tr.DIR)]
        group_context.BASE = self.tmp
        group_context.GROUPS_DIR = self.tmp / "memory" / "groups"
        tr.DIR = self.tmp / "memory" / ".state" / "group_context"

    def tearDown(self):
        for mod, key, value in self._gc:
            setattr(mod, key, value)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _msg(self, peer, mid, *, topic=None, reply=None, text=""):
        group_context.observe_message(
            peer_id=peer, topic_id=topic, message_id=mid, sender_id=1,
            sender_name="Николай", reply_to_message_id=reply, timestamp=None,
            edited_at=None, text=text or f"сообщение {mid}", topic_title="",
            media="", outgoing=False)


class TestTheDigestKeepsEveryPlace(Base):
    """Обзор «где я ещё сейчас» терял настоящие темы форума и ветки неизвестных комнат.

    Схлопывание шло по ПРЕФИКСУ комнаты: оставляли один ключ на комнату и выбрасывали
    все остальные — в том числе те, которые тот же реестр честно отказался склеивать.
    Регрессия против дерева до правки; проверено адверсаркой тремя независимыми линзами.
    """

    def _digest(self, keys, *, exclude=None):
        import agent
        state = self.tmp / "state"
        state.mkdir(parents=True, exist_ok=True)
        meta = {key: {"last_ts": ts, "name": name, "is_dm": False}
                for key, (ts, name) in keys.items()}
        (state / "buf_meta.json").write_text(json.dumps(meta), encoding="utf-8")
        scratch = self.tmp / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        orig = (agent.STATE_DIR, agent.notes.SCRATCH_DIR)
        agent.STATE_DIR, agent.notes.SCRATCH_DIR = state, scratch
        try:
            for key in keys:
                agent.notes.append(key, f"строка про {key}")
            return agent.other_rooms_digest(exclude_chat_id=exclude)
        finally:
            agent.STATE_DIR, agent.notes.SCRATCH_DIR = orig

    def test_real_forum_topics_all_stay_visible(self):
        tr.observe(FORUM, kind="topic_opener_seen", since_message_id=1,
                   until_message_id=9999)
        tr.observe_branches(FORUM, {500: None})
        out = self._digest({
            FORUM: (100.0, "Грибница"),
            f"{FORUM}__topic__11": (200.0, "Грибница / Вход"),
            f"{FORUM}__topic__22": (300.0, "Грибница / Питание"),
            f"{FORUM}__topic__500": (400.0, "Грибница / General-ветка"),
        })
        self.assertIn("__topic__11", out, "настоящая тема форума — отдельное место")
        self.assertIn("__topic__22", out)

    def test_branches_of_an_unknown_room_stay_visible(self):
        out = self._digest({
            "-100888": (100.0, "комната"),
            "-100888__topic__7": (200.0, "её ветка"),
        })
        self.assertIn("-100888__topic__7", out,
                      "без свидетельства ничего не склеиваем — и ничего не прячем")
        self.assertIn("-100888", out)

    def test_a_place_is_not_lost_when_its_freshest_key_has_no_note(self):
        """Схлопывание стояло ДО отбора: пустая записка у самого свежего ключа — и всё
        место исчезало из «где я сейчас» (вторая адверсарка 25.07)."""
        import agent
        tr.observe(ROOM, kind="channel_forum_missing", since_message_id=1,
                   until_message_id=999999)
        state = self.tmp / "state2"
        state.mkdir(parents=True, exist_ok=True)
        (state / "buf_meta.json").write_text(json.dumps({
            ROOM: {"last_ts": 100.0, "name": "AbstractDL", "is_dm": False},
            f"{ROOM}__topic__9": {"last_ts": 900.0, "name": "AbstractDL", "is_dm": False},
        }), encoding="utf-8")
        scratch = self.tmp / "scratch2"
        scratch.mkdir(parents=True, exist_ok=True)
        orig = (agent.STATE_DIR, agent.notes.SCRATCH_DIR)
        agent.STATE_DIR, agent.notes.SCRATCH_DIR = state, scratch
        try:
            agent.notes.append(ROOM, "живая записка в корне")   # у свежего ключа записки нет
            out = agent.other_rooms_digest()
        finally:
            agent.STATE_DIR, agent.notes.SCRATCH_DIR = orig
        self.assertIn("живая записка в корне", out,
                      "место остаётся видимым по той ветке, у которой есть что показать")

    def test_a_proven_room_still_collapses_to_one_line(self):
        tr.observe(ROOM, kind="channel_forum_missing", since_message_id=1,
                   until_message_id=999999)
        out = self._digest({
            ROOM: (100.0, "AbstractDL"),
            f"{ROOM}__topic__1": (200.0, "AbstractDL"),
            f"{ROOM}__topic__2": (300.0, "AbstractDL"),
        })
        self.assertEqual(out.count("AbstractDL"), 1, "одно место — одна строка")


class TestWideningNeverShowsLess(Base):
    """«Строго добавление» — обещание, которое надо выполнять, а не декларировать.

    Расширение места складывает в срез сотни соседних строк, и общий потолок начинал
    выбрасывать её собственную ветку, чтобы уместить соседей.
    """

    def _room(self):
        for i in range(40):
            self._msg(ROOM, 1000 + i, topic=None, text=f"сосед {i} " + "ы" * 400)
        self._msg(ROOM, 100, topic=None, text="корень её ветки")
        for i in range(8):
            self._msg(ROOM, 200 + i, topic=100, reply=100, text=f"её строка {i}")
        tr.observe(ROOM, kind="channel_forum_missing", since_message_id=1,
                   until_message_id=999999)

    def test_her_own_branch_survives_a_tight_budget(self):
        self._room()
        scope = tr.read_scope(ROOM, 100)
        narrow = group_context.context(ROOM, topic_id=100, limit=200, max_chars=40000)
        wide = group_context.context(ROOM, topic_id=100, limit=200, max_chars=4000,
                                     whole_room=scope.whole_room, members=scope.members,
                                     thread_word=scope.thread_word)
        for i in range(8):
            self.assertIn(f"её строка {i}", wide,
                          "соседи занимают ОСТАТОК бюджета, а не место её ветки")
        self.assertIn("корень её ветки", wide)
        self.assertTrue(len(narrow) > 0)

    def test_a_tight_cap_keeps_her_branch_too(self):
        self._room()
        scope = tr.read_scope(ROOM, 100)
        wide = group_context.context(ROOM, topic_id=100, limit=10, max_chars=40000,
                                     whole_room=scope.whole_room, members=scope.members,
                                     thread_word=scope.thread_word)
        self.assertIn("её строка 7", wide)
        self.assertIn("корень её ветки", wide)

    def test_reservation_works_for_the_root_branch_too(self):
        """General форума — корневая ветка, `topic_id` пуст. Резервирование выключалось
        целиком ровно в том случае, ради которого делалось (вторая адверсарка)."""
        for i in range(30):
            self._msg(FORUM, 1000 + i, topic=777, text=f"сосед {i} " + "ы" * 300)
        for i in range(6):
            self._msg(FORUM, 100 + i, topic=None, text=f"её строка General {i}")
        tr.observe(FORUM, kind="topic_opener_seen", since_message_id=1,
                   until_message_id=9999)
        tr.observe_branches(FORUM, {777: None})
        scope = tr.read_scope(FORUM, None)
        self.assertIsNotNone(scope.members)
        out = group_context.context(FORUM, topic_id=None, limit=200, max_chars=3000,
                                    whole_room=scope.whole_room, members=scope.members,
                                    thread_word=scope.thread_word)
        for i in range(6):
            self.assertIn(f"её строка General {i}", out)

    def test_the_cap_stays_a_cap_when_her_branch_fills_it(self):
        """`[-max(0, cap - room):]` — это `[-0:]`, то есть ВЕСЬ список, а не пустой."""
        for i in range(30):
            self._msg(ROOM, 2000 + i, topic=None, text=f"сосед {i}")
        for i in range(12):
            self._msg(ROOM, 100 + i, topic=100 if i else None,
                      reply=100 if i else None, text=f"её строка {i}")
        tr.observe(ROOM, kind="channel_forum_missing", since_message_id=1,
                   until_message_id=999999)
        scope = tr.read_scope(ROOM, 100)
        out = group_context.context(ROOM, topic_id=100, limit=5, max_chars=40000,
                                    whole_room=scope.whole_room, members=scope.members,
                                    thread_word=scope.thread_word)
        rows = [l for l in out.splitlines() if l.startswith("[")]
        self.assertLessEqual(len(rows), 6, "потолок остаётся потолком")
        self.assertIn("её строка 11", out, "и занимает его её собственная ветка")

    def test_her_newest_line_keeps_the_wide_render(self):
        """Широкий рендер — тоже ресурс: соседи не занимают его вперёд неё."""
        long_text = "ц" * 3000
        for i in range(6):
            self._msg(ROOM, 3000 + i, topic=None, text=f"сосед {i} " + "ы" * 100)
        self._msg(ROOM, 100, topic=None, text="корень")
        self._msg(ROOM, 101, topic=100, reply=100, text="её длинная реплика " + long_text)
        tr.observe(ROOM, kind="channel_forum_missing", since_message_id=1,
                   until_message_id=999999)
        scope = tr.read_scope(ROOM, 100)
        out = group_context.context(ROOM, topic_id=100, limit=200, max_chars=40000,
                                    whole_room=scope.whole_room, members=scope.members,
                                    thread_word=scope.thread_word)
        line = next(l for l in out.splitlines() if "её длинная реплика" in l)
        self.assertNotIn("ОБРЕЗАНО", line,
                         "её свежая реплика не обрезается ради соседских строк")

    def test_the_cut_says_which_side_was_cut(self):
        self._room()
        scope = tr.read_scope(ROOM, 100)
        wide = group_context.context(ROOM, topic_id=100, limit=200, max_chars=4000,
                                     whole_room=scope.whole_room, members=scope.members,
                                     thread_word=scope.thread_word)
        self.assertIn("ЛЕНТА ОБРЕЗАНА", wide)
        self.assertIn("в соседних ветках", wide, "обрез назван по имени, а не молча")
        # ⚠ Счёт идёт от ВСЕГО места, а не от уже обрезанного среза: первая версия
        # маркера (моя) считала остаток внутри `selected`, урезанного потолком, и
        # обещала «ещё 49», когда там были тысячи. И совет был ложный — упирается
        # бюджет символов, а не limit (вторая адверсарка + опись 26.07).
        self.assertNotIn("бо́льшим limit", wide, "ложный совет убран")
        self.assertIn("context_summary_chars", wide, "сказано, что реально поднимать")


class TestOrientationMatchesWhatSheGets(Base):
    """Строка ориентации — самое авторитетное предложение промпта про эту комнату."""

    def test_forum_general_is_not_called_isolated(self):
        tr.observe(FORUM, kind="topic_opener_seen", since_message_id=1,
                   until_message_id=9999)
        tr.observe_branches(FORUM, {100: None, 300: None})
        line = tr.orientation_line(FORUM, 100, "sel", tr.TRUE)
        self.assertNotIn("isolated from every other topic", line,
                         "именно здесь мы её ветку и склеили с General")
        self.assertIn("General", line)
        scope = tr.read_scope(FORUM, 100)
        self.assertIn(300, scope.members, "и склеили ровно так, как сказали")

    def test_a_real_forum_topic_is_still_called_isolated(self):
        tr.observe(FORUM, kind="topic_opener_seen", since_message_id=1,
                   until_message_id=9999)
        tr.observe_branches(FORUM, {100: None})
        line = tr.orientation_line(FORUM, 5, "sel", tr.TRUE)
        self.assertIn("isolated from every other topic", line)

    def test_an_ordinary_room_says_it_is_one_room(self):
        tr.observe(ROOM, kind="channel_forum_missing", since_message_id=1,
                   until_message_id=999999)
        line = tr.orientation_line(ROOM, 100, "sel", tr.FALSE)
        self.assertIn("NOT a Telegram forum topic", line)


class TestTheMapNamesEachBranchByItsOwnNature(Base):
    def test_a_real_topic_is_never_renamed_to_a_reply_thread(self):
        group_context.record_topic(FORUM, 5, "Вход", message_id=5)
        self._msg(FORUM, 5, topic=5, text="создана тема")
        self._msg(FORUM, 6, topic=5)
        self._msg(FORUM, 100, topic=None)
        self._msg(FORUM, 101, topic=100, reply=100)
        tr.observe(FORUM, kind="topic_opener_seen", since_message_id=1,
                   until_message_id=9999)
        tr.observe_branches(FORUM, group_context.branch_containers(FORUM))
        out = group_context.map_text(FORUM, current_topic=100,
                                     artifacts=tr.artifacts_of(FORUM))
        self.assertIn("topic #5", out, "тему форума провёл Telegram")
        self.assertIn("reply thread #100", out, "а эту ветку завели мы")

    def test_a_proven_ordinary_room_calls_them_all_ours(self):
        self._msg(ROOM, 100, topic=None)
        self._msg(ROOM, 101, topic=100, reply=100)
        out = group_context.map_text(ROOM, not_a_forum=True)
        self.assertIn("reply thread #100", out)
        self.assertNotIn("topic #100", out)


class TestNothingElseInTheStateDirIsTouched(unittest.TestCase):
    """`.state/life` — общий каталог. Заявка на переплавку — не разговор и невосстановима."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="praxis_state_"))
        self._orig = (ml.STATE_DIR, ml.MEM_DIR, ml.LIFE_DIR)
        ml.MEM_DIR = self.tmp / "memory"
        ml.LIFE_DIR = ml.MEM_DIR / "life"
        ml.STATE_DIR = ml.MEM_DIR / ".state" / "life"
        ml.STATE_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        ml.STATE_DIR, ml.MEM_DIR, ml.LIFE_DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_formation_request_is_not_a_conversation(self):
        request = ml.STATE_DIR / "formation.request.json"
        request.write_text(json.dumps({"depth": "full", "requested_at": "сейчас"}),
                           encoding="utf-8")
        (ml.STATE_DIR / "-100777.json").write_text(
            json.dumps({"schema": 1, "chat_id": "-100777", "hot": [], "frontier": []}),
            encoding="utf-8")
        self.assertFalse(ml.is_state_file(request))
        self.assertEqual(ml.state_keys(), ["-100777"])
        out = ml.retire_split_states()
        self.assertEqual(out["moved"], [])
        self.assertTrue(request.exists(), "её заявку на переплавку никто не отодвигает")

    def test_rollback_asks_the_same_question_as_retirement(self):
        """Откат удалял ЛЮБОЙ появившийся json — включая заявку на переплавку."""
        import migrate_places
        attic = ml.STATE_DIR / "_pre_places"
        attic.mkdir(parents=True, exist_ok=True)
        (attic / "-100777.json").write_text(
            json.dumps({"schema": 1, "chat_id": "-100777", "hot": [], "frontier": []}),
            encoding="utf-8")
        (ml.STATE_DIR / "-100777.json").write_text(
            json.dumps({"schema": 1, "chat_id": "-100777", "hot": [1], "frontier": []}),
            encoding="utf-8")
        request = ml.STATE_DIR / "formation.request.json"
        request.write_text(json.dumps({"depth": "full"}), encoding="utf-8")
        appeared = ml.STATE_DIR / "-100888.json"
        appeared.write_text(
            json.dumps({"schema": 1, "chat_id": "-100888", "hot": [], "frontier": []}),
            encoding="utf-8")

        class _Routes:
            DIR = ml.STATE_DIR / "no_such_registry"

        out = migrate_places.rollback(ml, _Routes)
        self.assertTrue(request.exists(), "её заявку откат не трогает")
        self.assertFalse(appeared.exists(), "а появившееся состояние — убирает")
        self.assertIn("-100777", out["restored"])

    def test_a_corrupt_file_is_left_alone(self):
        broken = ml.STATE_DIR / "-100888__topic__5.json"
        broken.write_text("{не json", encoding="utf-8")
        self.assertEqual(ml.retire_split_states()["moved"], [])
        self.assertTrue(broken.exists())


class TestPeerScopedCanary(Base):
    """Канарейка `--peer` (22.08): пересчёт одной комнаты не смеет коснуться чужих.

    Требования её ответа 22.08: read-only dry-run; доказательство, что записи не
    происходит даже без `--peer`; настоящий прогон и откат — строго в границе пира;
    снимок «до» — первый свидетель на каждый файл, июльский чердак не переписывается.
    """

    A = ROOM                       # AbstractDL — канарейка №1
    B = "-1002056836363"

    def setUp(self):
        super().setUp()
        self._ml = [(ml, name, getattr(ml, name))
                    for name in ("MEM_DIR", "LIFE_DIR", "EVENTS_DIR",
                                 "COMPACTS_DIR", "STATE_DIR")]
        ml.MEM_DIR = self.tmp / "memory"
        ml.LIFE_DIR = ml.MEM_DIR / "life"
        ml.EVENTS_DIR = ml.LIFE_DIR / "events"
        ml.COMPACTS_DIR = ml.LIFE_DIR / "compacts"
        ml.STATE_DIR = ml.MEM_DIR / ".state" / "life"
        ml.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._env_base = os.environ.get("PRAXIS_BASE")

    def tearDown(self):
        for mod, key, value in self._ml:
            setattr(mod, key, value)
        if self._env_base is None:
            os.environ.pop("PRAXIS_BASE", None)
        else:
            os.environ["PRAXIS_BASE"] = self._env_base
        super().tearDown()

    def _state(self, key, hot=()):
        (ml.STATE_DIR / f"{key}.json").write_text(json.dumps(
            {"schema": 1, "chat_id": str(key), "hot": list(hot), "frontier": []}),
            encoding="utf-8")

    def _tree_hash(self) -> dict:
        return {str(path.relative_to(self.tmp)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(self.tmp.rglob("*")) if path.is_file()}

    def _two_rooms(self):
        self._msg(self.A, 100, topic=None, text="корень")
        self._msg(self.A, 101, topic=100, reply=100, text="ответ")
        self._msg(self.B, 200, topic=None, text="корень B")
        self._msg(self.B, 201, topic=200, reply=200, text="ответ B")
        self._state(self.A)
        self._state(f"{self.A}__topic__100", hot=[1])
        self._state(self.B)
        self._state(f"{self.B}__topic__200", hot=[2])
        # REPAIR d2e: посев больше не пишет FALSE из тишины — сведение обычной
        # комнаты держится на честном живом свидетельстве, как в проде.
        tr.observe(self.A, kind="channel_forum_missing", since_message_id=1,
                   until_message_id=999999)
        tr.observe(self.B, kind="channel_forum_missing", since_message_id=1,
                   until_message_id=999999)

    def _main(self, *argv) -> tuple[int, str]:
        import migrate_places
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = migrate_places.main(list(argv))
        return code, buf.getvalue()

    def _runs(self, peer):
        import migrate_places
        return migrate_places.committed_runs(ml, peer)

    def test_dry_run_writes_nothing_with_and_without_peer(self):
        """Её слово дословно: доказательство, что записи нет ДАЖЕ без `--peer`."""
        self._two_rooms()
        tr.observe(self.A, kind="channel_forum_missing", since_message_id=1,
                   until_message_id=999999)
        before = self._tree_hash()
        for argv in (("--base", str(self.tmp), "--dry-run"),
                     ("--base", str(self.tmp), "--dry-run", "--peer", self.A)):
            code, out = self._main(*argv)
            self.assertEqual(code, 0, out)
            self.assertEqual(self._tree_hash(), before,
                             f"сухой прогон {argv} написал на диск")

    def test_dry_run_and_rollback_are_rejected_before_any_write(self):
        """Противоречившие режимы не должны даже загружать модуль миграции."""
        import migrate_places
        self._two_rooms()
        before = self._tree_hash()
        stderr = io.StringIO()
        with mock.patch.object(migrate_places, "_modules") as modules, \
             contextlib.redirect_stderr(stderr), \
             self.assertRaises(SystemExit) as stopped:
            migrate_places.main(["--base", str(self.tmp), "--dry-run", "--rollback"])

        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("not allowed with argument", stderr.getvalue())
        modules.assert_not_called()
        self.assertEqual(self._tree_hash(), before,
                         "конфликт --dry-run/--rollback что-то записал")

    def test_a_real_canary_run_stays_inside_its_peer(self):
        self._two_rooms()
        tr.observe(self.B, kind="channel_forum_missing", since_message_id=1,
                   until_message_id=999999)
        route_b = (tr.DIR / f"{self.B}.route.json").read_bytes()
        state_b = (ml.STATE_DIR / f"{self.B}__topic__200.json").read_bytes()
        base_root = (ml.STATE_DIR / f"{self.A}.json").read_bytes()
        base_topic = (ml.STATE_DIR / f"{self.A}__topic__100.json").read_bytes()

        code, out = self._main("--base", str(self.tmp), "--peer", self.A)
        self.assertEqual(code, 0, out)

        runs = self._runs(self.A)
        self.assertEqual(len(runs), 1, "прогон закоммитил собственную базу")
        names = {f["name"] for f in runs[0]["files"]}
        self.assertEqual(names, {f"{self.A}.json", f"{self.A}__topic__100.json"},
                         "база полная: ВСЕ состояния пира, без пропусков")
        by_name = {f["name"]: f["sha256"] for f in runs[0]["files"]}
        self.assertEqual(by_name[f"{self.A}.json"],
                         hashlib.sha256(base_root).hexdigest())
        self.assertEqual(by_name[f"{self.A}__topic__100.json"],
                         hashlib.sha256(base_topic).hexdigest())

        import migrate_places
        run_dir = migrate_places._runs_root(ml) / str(runs[0]["run_id"])
        self.assertTrue((run_dir / "retired" / f"{self.A}__topic__100.json").exists(),
                        "расщеплённое состояние припарковано в СВОЙ прогон")
        attic = ml.STATE_DIR / "_pre_places"
        self.assertEqual(list(attic.glob("*.json")), [],
                         "в плоский чердак канарейка не написала ни файла")
        self.assertEqual(ml.bindings().get(f"{self.A}__topic__100"), self.A)
        self.assertEqual((tr.DIR / f"{self.B}.route.json").read_bytes(), route_b,
                         "чужой реестр не тронут ни байтом")
        self.assertEqual((ml.STATE_DIR / f"{self.B}__topic__200.json").read_bytes(),
                         state_b)
        self.assertEqual(self._runs(self.B), [], "у чужого пира прогонов не появилось")

    def test_legacy_files_cannot_poison_or_be_touched_by_a_new_snapshot(self):
        """Её контракт №1 (живой инцидент): июльский одноимённый файл в плоском
        чердаке больше не заставляет снимок «пропустить» корень — база всегда
        полная, а legacy не тронут ни байтом."""
        import migrate_places
        self._two_rooms()
        attic = ml.STATE_DIR / "_pre_places"
        attic.mkdir(parents=True, exist_ok=True)
        july_root = json.dumps({"schema": 1, "chat_id": self.A, "hot": ["июль"],
                                "frontier": []})
        (attic / f"{self.A}.json").write_text(july_root, encoding="utf-8")
        (attic / f"{self.A}__topic__old1.json").write_text(july_root, encoding="utf-8")
        (attic / "_canary_peers.txt").write_text(json.dumps([self.A]),
                                                 encoding="utf-8")
        live_root = (ml.STATE_DIR / f"{self.A}.json").read_bytes()

        migrate_places.snapshot_states(ml, only_peer=self.A, telegram_routes=tr)

        runs = self._runs(self.A)
        self.assertEqual(len(runs), 1,
                         "legacy-манифест старого мира не отменяет НОВУЮ базу")
        names = {f["name"] for f in runs[0]["files"]}
        self.assertIn(f"{self.A}.json", names,
                      "корень НЕ пропущен из-за одноимённого июльского файла")
        entry = next(f for f in runs[0]["files"] if f["name"] == f"{self.A}.json")
        self.assertEqual(entry["sha256"], hashlib.sha256(live_root).hexdigest(),
                         "в базе — ЖИВОЙ корень, не июльский")
        self.assertEqual((attic / f"{self.A}.json").read_text(encoding="utf-8"),
                         july_root, "июльский файл не переписан")
        self.assertTrue((attic / f"{self.A}__topic__old1.json").exists())
        self.assertEqual(
            json.loads((attic / "_canary_peers.txt").read_text(encoding="utf-8")),
            [self.A], "legacy-манифест не тронут")

    def test_rollback_never_lifts_legacy_files_of_the_peer(self):
        """Её контракт №2 (живой инцидент): откат прогона A не смеет поднять
        чужие июльские файлы того же пира — только baseline своего manifest."""
        import migrate_places
        self._two_rooms()
        tr.observe(self.A, kind="channel_forum_missing", since_message_id=1,
                   until_message_id=999999)
        attic = ml.STATE_DIR / "_pre_places"
        attic.mkdir(parents=True, exist_ok=True)
        july_root = json.dumps({"schema": 1, "chat_id": self.A, "hot": ["июль"],
                                "frontier": []})
        for name in (f"{self.A}.json", f"{self.A}__topic__old1.json",
                     f"{self.A}__topic__old2.json"):
            (attic / name).write_text(july_root, encoding="utf-8")
        (attic / "_canary_peers.txt").write_text(json.dumps([self.A]),
                                                 encoding="utf-8")
        root_t0 = (ml.STATE_DIR / f"{self.A}.json").read_bytes()

        migrate_places.snapshot_states(ml, only_peer=self.A, telegram_routes=tr)
        self._state(self.A, hot=["T1"])
        self._state(f"{self.A}__topic__999", hot=["поздний"])

        out = migrate_places.rollback(ml, tr, only_peer=self.A)

        self.assertEqual((ml.STATE_DIR / f"{self.A}.json").read_bytes(), root_t0,
                         "вернулась база ПРОГОНА, а не июльская эпоха")
        self.assertIn(f"{self.A}__topic__999", out["dropped"])
        self.assertFalse((ml.STATE_DIR / f"{self.A}__topic__old1.json").exists(),
                         "чужая эпоха не поднята в живые состояния")
        self.assertFalse((ml.STATE_DIR / f"{self.A}__topic__old2.json").exists())
        self.assertTrue((attic / f"{self.A}__topic__old1.json").exists(),
                        "июльские файлы остались лежать в чердаке")
        self.assertTrue((attic / f"{self.A}__topic__old2.json").exists())
        self.assertEqual((attic / f"{self.A}.json").read_text(encoding="utf-8"),
                         july_root, "июльский корень в чердаке не переписан")

    def test_partial_run_never_becomes_a_base_for_another_run(self):
        """Её контракт №3: обрыв до коммита + июльская коллизия. Осколки
        оборванной попытки не наследуются, новый прогон снимает ПОЛНУЮ базу
        момента своего старта — и откат возвращает именно её."""
        import migrate_places
        self._two_rooms()
        tr.observe(self.A, kind="channel_forum_missing", since_message_id=1,
                   until_message_id=999999)
        attic = ml.STATE_DIR / "_pre_places"
        attic.mkdir(parents=True, exist_ok=True)
        july_root = json.dumps({"schema": 1, "chat_id": self.A, "hot": ["июль"],
                                "frontier": []})
        (attic / f"{self.A}.json").write_text(july_root, encoding="utf-8")

        seam = ("_commit_run_manifest"
                if hasattr(migrate_places, "_commit_run_manifest")
                else "_canary_note")
        with mock.patch.object(migrate_places, seam,
                               side_effect=OSError("обрыв до коммита")):
            with self.assertRaises(OSError):
                migrate_places.snapshot_states(ml, only_peer=self.A, telegram_routes=tr)

        self._state(self.A, hot=["T2"])
        self._state(f"{self.A}__topic__999", hot=["поздний"])

        migrate_places.snapshot_states(ml, only_peer=self.A, telegram_routes=tr)
        out = migrate_places.rollback(ml, tr, only_peer=self.A)

        self.assertFalse(out.get("states_untouched"), out)
        root = json.loads((ml.STATE_DIR / f"{self.A}.json").read_text(encoding="utf-8"))
        self.assertEqual(root["hot"], ["T2"],
                         "база второго прогона — момент ЕГО старта: ни осколков "
                         "обрыва, ни июльской эпохи")
        self.assertTrue((ml.STATE_DIR / f"{self.A}__topic__999.json").exists(),
                        "поздняя ветка — часть базы второго прогона")

    def test_exact_rollback_returns_only_the_baseline_of_the_named_run(self):
        """Её контракт №2/№4: несколько прогонов — откат строго по run_id;
        неоднозначность без --run — полный отказ; архивы прогонов переживают
        откат (восстановление копированием)."""
        import migrate_places
        self._msg(self.A, 100, topic=None, text="корень")
        self._state(self.A, hot=["T0"])

        first = migrate_places.snapshot_states(ml, only_peer=self.A, telegram_routes=tr)
        self.assertIsInstance(first, tuple,
                              "каждый прогон коммитит СОБСТВЕННУЮ базу")
        run1 = first[1]
        self._state(self.A, hot=["T1"])
        run2 = migrate_places.snapshot_states(ml, only_peer=self.A, telegram_routes=tr)[1]
        self.assertEqual(len(self._runs(self.A)), 2)
        self._state(self.A, hot=["T2"])

        refuse = migrate_places.rollback(ml, tr, only_peer=self.A)
        self.assertTrue(refuse["states_untouched"],
                        "неоднозначность без --run — полный отказ")
        live = json.loads((ml.STATE_DIR / f"{self.A}.json").read_text(encoding="utf-8"))
        self.assertEqual(live["hot"], ["T2"], "отказ не тронул ни байта")

        unknown = migrate_places.rollback(ml, tr, only_peer=self.A,
                                          run_id="нет-такого")
        self.assertTrue(unknown["states_untouched"],
                        "незнакомый run_id — полный отказ")

        out2 = migrate_places.rollback(ml, tr, only_peer=self.A, run_id=run2)
        self.assertEqual(out2["run_id"], run2)
        live = json.loads((ml.STATE_DIR / f"{self.A}.json").read_text(encoding="utf-8"))
        self.assertEqual(live["hot"], ["T1"], "откат run2 вернул ровно ЕГО базу")

        out1 = migrate_places.rollback(ml, tr, only_peer=self.A, run_id=run1)
        self.assertEqual(out1["run_id"], run1)
        live = json.loads((ml.STATE_DIR / f"{self.A}.json").read_text(encoding="utf-8"))
        self.assertEqual(live["hot"], ["T0"], "откат run1 вернул ровно ЕГО базу")

        self.assertEqual(len(self._runs(self.A)), 2,
                         "архивы прогонов целы: откат копирует, не выносит")

    def test_rollback_without_a_committed_snapshot_leaves_states_alone(self):
        """Обрыв до коммита и сразу откат: базы НЕТ — состояния не трогаются,
        снимается только реестровое свидетельство, .rev живёт."""
        import migrate_places
        self._msg(self.A, 100, topic=None, text="корень")
        tr.observe(self.A, kind="channel_forum_missing", since_message_id=1,
                   until_message_id=999999)
        self._state(self.A, hot=["живое"])

        seam = ("_commit_run_manifest"
                if hasattr(migrate_places, "_commit_run_manifest")
                else "_canary_note")
        with mock.patch.object(migrate_places, seam,
                               side_effect=OSError("обрыв до коммита")):
            with self.assertRaises(OSError):
                migrate_places.snapshot_states(ml, only_peer=self.A, telegram_routes=tr)
        self._state(f"{self.A}__topic__999", hot=["поздний"])

        out = migrate_places.rollback(ml, tr, only_peer=self.A)

        self.assertEqual(out["restored"], [], "восстанавливать нечего — базы не было")
        self.assertEqual(out["dropped"], [], "и удалять не по чему")
        self.assertTrue(out.get("states_untouched"))
        live = json.loads((ml.STATE_DIR / f"{self.A}.json").read_text(encoding="utf-8"))
        self.assertEqual(live["hot"], ["живое"])
        self.assertTrue((ml.STATE_DIR / f"{self.A}__topic__999.json").exists())
        self.assertFalse((tr.DIR / f"{self.A}.route.json").exists(),
                         "свидетельства реестра откат снимает как обычно")
        self.assertTrue((tr.DIR / f"{self.A}.route.rev").exists())

    def test_two_racing_snapshot_callers_each_commit_their_own_base(self):
        """Гонка двух конкурентов ОДНОГО пира: per-peer замок сериализует их, и
        каждый коммитит собственный полный прогон — терять базу больше нечему."""
        import threading
        import migrate_places
        self._state(self.A, hot=["корень"])
        self._state(f"{self.A}__topic__10", hot=["ветка"])
        self._msg(self.A, 100, topic=None, text="корень")
        runs_root = ml.STATE_DIR / "_pre_places" / "runs"
        topic_name = f"{self.A}__topic__10.json"

        a_mid, a_go, b_attempt = (threading.Event() for _ in range(3))
        results: dict = {}
        real_write = Path.write_bytes

        def hooked_write(path_self, data):
            out = real_write(path_self, data)
            if (path_self.name == topic_name and runs_root in path_self.parents
                    and threading.current_thread().name == "canary-A"):
                a_mid.set()
                a_go.wait(15)
            return out

        def snap(tag):
            try:
                results[tag] = migrate_places.snapshot_states(ml, only_peer=self.A, telegram_routes=tr)
            except OSError as err:
                results[tag] = err

        real_lock = migrate_places._peer_lock

        @contextlib.contextmanager
        def noisy_lock(mlmod, peer):
            if threading.current_thread().name == "canary-B":
                b_attempt.set()
            with real_lock(mlmod, peer):
                yield

        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(Path, "write_bytes", hooked_write))
        stack.enter_context(mock.patch.object(migrate_places, "_peer_lock",
                                              noisy_lock))
        with stack:
            thread_a = threading.Thread(target=snap, args=("A",), name="canary-A")
            thread_a.start()
            self.assertTrue(a_mid.wait(15), "A должен замереть между копиями")
            thread_b = threading.Thread(target=snap, args=("B",), name="canary-B")
            thread_b.start()
            self.assertTrue(b_attempt.wait(15))
            a_go.set()
            thread_a.join(15)
            thread_b.join(15)
            self.assertFalse(thread_a.is_alive())
            self.assertFalse(thread_b.is_alive())

        runs = self._runs(self.A)
        self.assertEqual(len(runs), 2, f"каждый конкурент закоммитил свой прогон: "
                                       f"{results}")
        for manifest in runs:
            self.assertEqual({f["name"] for f in manifest["files"]},
                             {f"{self.A}.json", topic_name},
                             "обе базы полные — ни одного потерянного файла")

    def test_concurrent_snapshots_of_two_peers_commit_isolated_bases(self):
        """Разные пиры: namespace-прогоны не делят ни одного общего файла —
        коммиты не могут затирать друг друга по построению."""
        import migrate_places
        room_a = json.dumps({"schema": 1, "chat_id": self.A, "hot": ["A"],
                             "frontier": []})
        room_b = json.dumps({"schema": 1, "chat_id": self.B, "hot": ["B"],
                             "frontier": []})
        (ml.STATE_DIR / f"{self.A}.json").write_text(room_a, encoding="utf-8")
        (ml.STATE_DIR / f"{self.B}.json").write_text(room_b, encoding="utf-8")

        run_a = migrate_places.snapshot_states(ml, only_peer=self.A, telegram_routes=tr)[1]
        run_b = migrate_places.snapshot_states(ml, only_peer=self.B, telegram_routes=tr)[1]

        runs_a, runs_b = self._runs(self.A), self._runs(self.B)
        self.assertEqual([m["run_id"] for m in runs_a], [run_a])
        self.assertEqual([m["run_id"] for m in runs_b], [run_b])
        self.assertEqual({f["name"] for f in runs_a[0]["files"]},
                         {f"{self.A}.json"}, "в прогоне A нет чужих файлов")
        self.assertEqual({f["name"] for f in runs_b[0]["files"]},
                         {f"{self.B}.json"})

        for peer, body, rid in ((self.A, room_a, run_a), (self.B, room_b, run_b)):
            (ml.STATE_DIR / f"{peer}.json").write_text(
                json.dumps({"schema": 1, "chat_id": peer, "hot": ["изменилось"],
                            "frontier": []}), encoding="utf-8")
            out = migrate_places.rollback(ml, tr, only_peer=peer, run_id=rid)
            self.assertIn(peer, out["restored"])
            self.assertEqual(
                (ml.STATE_DIR / f"{peer}.json").read_text(encoding="utf-8"),
                body, f"{peer}: откат вернул исходный baseline своего прогона")

    def _force_lost_events(self):
        """Настоящий провал verify: событие на диске + rebuild, который его
        теряет (ровно живой инцидент: rebuild переписал root без 5453 событий)."""
        ml.EVENTS_DIR.mkdir(parents=True, exist_ok=True)
        (ml.EVENTS_DIR / "2026-08-23.jsonl").write_text(
            json.dumps({"kind": "conversation_message", "chat_id": self.A,
                        "id": "evt-5453"}) + "\n", encoding="utf-8")

        def broken_rebuild(place):
            state = {"schema": 1, "chat_id": str(place), "hot": [],
                     "frontier": []}
            (ml.STATE_DIR / f"{place}.json").write_text(
                json.dumps(state), encoding="utf-8")
            return state

        return mock.patch.object(ml, "rebuild_state", side_effect=broken_rebuild)

    def test_invariant_failure_recovers_exactly_and_is_no_usable_canary(self):
        """Её REPAIR4 п.4 + наблюдённое условие: июльская коллизия root +
        НАСТОЯЩИЙ провал verify. Провал не оставляет «пригодной канарейки»:
        состояния и route возвращаются к базе прогона байт-в-байт, прогон
        помечен recovered, `--rollback --peer` по нему невозможен."""
        import migrate_places
        self._two_rooms()
        tr.observe(self.A, kind="channel_forum_missing", since_message_id=1,
                   until_message_id=50)          # до-прогонное знание в route
        attic = ml.STATE_DIR / "_pre_places"
        attic.mkdir(parents=True, exist_ok=True)
        july_root = json.dumps({"schema": 1, "chat_id": self.A, "hot": ["июль"],
                                "frontier": []})
        (attic / f"{self.A}.json").write_text(july_root, encoding="utf-8")
        for i in range(3):
            (attic / f"{self.A}__topic__old{i}.json").write_text(
                july_root, encoding="utf-8")
        legacy_before = {p.name: p.read_bytes() for p in attic.glob("*.json")}

        root_before = (ml.STATE_DIR / f"{self.A}.json").read_bytes()
        topic_before = (ml.STATE_DIR / f"{self.A}__topic__100.json").read_bytes()
        route_path = tr.DIR / f"{self.A}.route.json"
        route_before = route_path.read_bytes()

        with self._force_lost_events():
            code, out = self._main("--base", str(self.tmp), "--peer", self.A)
        self.assertEqual(code, 1, out)
        self.assertIn("потеряно", out, "провал verify настоящий")

        self.assertEqual((ml.STATE_DIR / f"{self.A}.json").read_bytes(),
                         root_before,
                         "живой root возвращён к базе прогона байт-в-байт")
        self.assertEqual(
            (ml.STATE_DIR / f"{self.A}__topic__100.json").read_bytes(),
            topic_before)
        self.assertEqual(route_path.read_bytes(), route_before,
                         "route-свидетельства этого прогона сняты: байты до-прогонные")
        self.assertTrue((tr.DIR / f"{self.A}.route.rev").exists(),
                        "durable-ревизия не откатывается")
        for name, body in legacy_before.items():
            self.assertEqual((attic / name).read_bytes(), body,
                             f"{name}: июльский чердак не тронут")

        self.assertNotIn(f"{self.A}__topic__100", ml.bindings(),
                         "привязка, добавленная провалившимся прогоном, снята "
                         "из журнала (place-мутаций снаружи не остаётся)")

        runs = self._runs(self.A)
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0].get("_recovered"),
                        "прогон помечен recovered — «пригодной канарейкой» не является")
        rid = str(runs[0]["run_id"])

        blind = migrate_places.rollback(ml, tr, only_peer=self.A)
        self.assertTrue(blind["states_untouched"],
                        "после провала --rollback --peer невозможен: выборка пуста")
        self.assertEqual(route_path.read_bytes(), route_before,
                         "отказ не снял до-прогонное знание реестра")
        explicit = migrate_places.rollback(ml, tr, only_peer=self.A, run_id=rid)
        self.assertTrue(explicit["states_untouched"])
        self.assertEqual(explicit.get("recovered_refused"), rid,
                         "явный --run по восстановленному прогону — отказ, не no-op")
        self.assertEqual((ml.STATE_DIR / f"{self.A}.json").read_bytes(),
                         root_before, "отказы не тронули ни байта")

    def test_crashed_recovery_is_finishable_via_explicit_run(self):
        """Обрыв самого восстановления: прогон остаётся пригодным, и явный
        `--rollback --peer --run <id>` идемпотентно доводит ту же базу."""
        import migrate_places
        self._two_rooms()
        root_before = (ml.STATE_DIR / f"{self.A}.json").read_bytes()

        seam = ("_mark_recovered" if hasattr(migrate_places, "_mark_recovered")
                else "_commit_run_manifest")
        with self._force_lost_events():
            with mock.patch.object(migrate_places, seam,
                                   side_effect=OSError("обрыв восстановления")):
                try:
                    self._main("--base", str(self.tmp), "--peer", self.A)
                except OSError:
                    pass
        runs = self._runs(self.A)
        self.assertEqual(len(runs), 1)
        self.assertFalse(runs[0].get("_recovered"),
                         "пометки нет — прогон остался пригодным для доводки")
        rid = str(runs[0]["run_id"])

        out = migrate_places.rollback(ml, tr, only_peer=self.A, run_id=rid)
        self.assertEqual(out["run_id"], rid)
        self.assertEqual((ml.STATE_DIR / f"{self.A}.json").read_bytes(),
                         root_before, "явная доводка вернула базу байт-в-байт")

    def test_failed_canary_leaves_no_binding_in_the_journal(self):
        """Её REPAIR5, блокер №1: провалившаяся канарейка не оставляет в журнале
        привязок СВОЕЙ дельты — а до-прогонные привязки не трогает."""
        self._two_rooms()
        ml.bind_place(self.A, [f"{self.A}__topic__55"])   # до-прогонная привязка

        with self._force_lost_events():
            code, out = self._main("--base", str(self.tmp), "--peer", self.A)
        self.assertEqual(code, 1, out)
        self.assertIn("потеряно", out)

        bound = ml.bindings()
        self.assertNotIn(f"{self.A}__topic__100", bound,
                         "дельта провалившегося прогона снята из журнала")
        self.assertEqual(bound.get(f"{self.A}__topic__55"), self.A,
                         "до-прогонная привязка пережила восстановление")

    def test_crash_between_state_and_route_recovery_is_finishable_exactly(self):
        """Её REPAIR5, блокер №2: обрыв ПОСЛЕ восстановления state, но ДО route.
        Явная доводка обязана поднять route-БАЗУ прогона (не удалить route,
        теряя до-прогонные байты) и снять дельту привязок."""
        import migrate_places
        self._two_rooms()
        tr.observe(self.A, kind="channel_forum_missing", since_message_id=1,
                   until_message_id=50)          # до-прогонное знание в route
        route_path = tr.DIR / f"{self.A}.route.json"
        route_before = route_path.read_bytes()
        root_before = (ml.STATE_DIR / f"{self.A}.json").read_bytes()

        real_restore = migrate_places._restore_states

        def crash_after_states(memory_life, peer, payload, out):
            real_restore(memory_life, peer, payload, out)
            raise OSError("обрыв между state и route")

        with self._force_lost_events():
            with mock.patch.object(migrate_places, "_restore_states",
                                   side_effect=crash_after_states):
                try:
                    self._main("--base", str(self.tmp), "--peer", self.A)
                except OSError:
                    pass

        runs = self._runs(self.A)
        self.assertEqual(len(runs), 1)
        self.assertFalse(runs[0].get("_recovered"),
                         "восстановление оборвано — пометки нет, прогон пригоден")
        rid = str(runs[0]["run_id"])
        # разрыв настоящий: state уже базовый, route ещё пост-прогонный
        self.assertEqual((ml.STATE_DIR / f"{self.A}.json").read_bytes(),
                         root_before)
        self.assertNotEqual(route_path.read_bytes(), route_before)

        out = migrate_places.rollback(ml, tr, only_peer=self.A, run_id=rid)
        self.assertEqual(out["run_id"], rid)
        self.assertTrue(route_path.exists(),
                        "route не удалён, а восстановлен из базы прогона")
        self.assertEqual(route_path.read_bytes(), route_before,
                         "доводка подняла до-прогонные байты route")
        self.assertNotIn(f"{self.A}__topic__100", ml.bindings(),
                         "доводка сняла дельту привязок провалившегося прогона")
        self.assertTrue(self._runs(self.A)[0].get("_recovered"),
                        "после доводки прогон помечен recovered")

    def test_recovery_removes_only_bindings_authored_by_the_run(self):
        """Её REPAIR6 P0 дословно: после снимка независимый внешний актор
        добавляет свою привязку — восстановление провалившегося прогона обязано
        снять ТОЛЬКО своё (по receipt), а чужую параллельную оставить жить."""
        self._two_rooms()
        ml.bind_place(self.A, [f"{self.A}__topic__55"])   # до-прогонная

        ml.EVENTS_DIR.mkdir(parents=True, exist_ok=True)
        (ml.EVENTS_DIR / "2026-08-23.jsonl").write_text(
            json.dumps({"kind": "conversation_message", "chat_id": self.A,
                        "id": "evt-5453"}) + "\n", encoding="utf-8")

        def rebuild_and_external_actor(place):
            # внешний актор успевает МЕЖДУ снимком и восстановлением
            ml.bind_place(self.A, [f"{self.A}__topic__999"])
            state = {"schema": 1, "chat_id": str(place), "hot": [],
                     "frontier": []}
            (ml.STATE_DIR / f"{place}.json").write_text(
                json.dumps(state), encoding="utf-8")
            return state

        with mock.patch.object(ml, "rebuild_state",
                               side_effect=rebuild_and_external_actor):
            code, out = self._main("--base", str(self.tmp), "--peer", self.A)
        self.assertEqual(code, 1, out)

        bound = ml.bindings()
        self.assertEqual(bound.get(f"{self.A}__topic__999"), self.A,
                         "ЧУЖАЯ параллельная привязка пережила восстановление")
        self.assertNotIn(f"{self.A}__topic__100", bound,
                         "собственная привязка прогона снята по receipt")
        self.assertEqual(bound.get(f"{self.A}__topic__55"), self.A,
                         "до-прогонная привязка не тронута")

    def test_same_key_race_between_plan_and_write_is_not_claimed(self):
        """Её REPAIR7 P0 дословно: независимый writer кладёт ТУ ЖЕ пару
        key→place между планом и записью. bind_place канарейки возвращает 0 —
        пара не попадает в receipt-ФАКТ, и восстановление обязано оставить
        чужую одноимённую привязку жить (receipt-намерение ≠ авторство)."""
        self._two_rooms()
        ml.EVENTS_DIR.mkdir(parents=True, exist_ok=True)
        (ml.EVENTS_DIR / "2026-08-23.jsonl").write_text(
            json.dumps({"kind": "conversation_message", "chat_id": self.A,
                        "id": "evt-5453"}) + "\n", encoding="utf-8")

        def broken_rebuild(place):
            state = {"schema": 1, "chat_id": str(place), "hot": [],
                     "frontier": []}
            (ml.STATE_DIR / f"{place}.json").write_text(
                json.dumps(state), encoding="utf-8")
            return state

        import migrate_places
        real_receipt = migrate_places._write_bindings_receipt
        fired = {"done": False}

        def racing_receipt(run_dir, status, entries):
            real_receipt(run_dir, status, entries)
            if status == "planned" and not fired["done"]:
                # независимый межпроцессный writer успевает положить ту же пару
                # между планом и записью канарейки
                fired["done"] = True
                ml.bind_place(self.A, [f"{self.A}__topic__100"])

        with mock.patch.object(ml, "rebuild_state", side_effect=broken_rebuild):
            with mock.patch.object(migrate_places, "_write_bindings_receipt",
                                   side_effect=racing_receipt):
                code, out = self._main("--base", str(self.tmp), "--peer", self.A)
        self.assertEqual(code, 1, out)
        self.assertIn("потеряно", out)

        self.assertEqual(ml.bindings().get(f"{self.A}__topic__100"), self.A,
                         "одноимённая ЧУЖАЯ привязка пережила восстановление: "
                         "намерение не есть доказанное авторство")

    _CHILD_BIND = (
        "import os, sys, pathlib, hashlib, json, importlib\n"
        "base, key, place, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]\n"
        "os.environ['PRAXIS_BASE'] = base\n"
        "os.environ.setdefault('PRAXIS_TEST', '1')\n"
        "root, expected = pathlib.Path(sys.argv[5]).resolve(), json.loads(sys.argv[6])\n"
        "sys.path.insert(0, str(root))\n"
        "actual = {}\n"
        "for name in expected:\n"
        "    module = importlib.import_module(name)\n"
        "    path = pathlib.Path(module.__file__).resolve()\n"
        "    actual[name] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}\n"
        "if actual != expected:\n"
        "    raise RuntimeError(f'child source identity mismatch: {actual!r} != {expected!r}')\n"
        "import memory_life\n"
        "ready = pathlib.Path(out + '.ready')\n"
        "staged = pathlib.Path(out + '.ready.tmp')\n"
        "staged.write_text(json.dumps(actual), encoding='utf-8')\n"
        "staged.replace(ready)\n"
        "added = memory_life.bind_place(place, [key])\n"
        "pathlib.Path(out).write_text(str(added), encoding='utf-8')\n"
    )

    def _child_source_identity(self):
        # Source root is independent of PRAXIS_BASE (disposable runtime data).
        root = Path(__file__).resolve().parent
        identity = {}
        for name in ("memory_life", "self_model", "memory_provenance"):
            module = __import__(name)
            path = Path(module.__file__).resolve()
            self.assertEqual(path, root / f"{name}.py",
                             "parent imported a different checkout")
            identity[name] = {"path": str(path),
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        return root, identity

    def _child_bind_command(self, key, out_path):
        root, identity = self._child_source_identity()
        return [sys.executable, "-c", self._CHILD_BIND, str(self.tmp),
                key, self.A, str(out_path), str(root), json.dumps(identity)]

    def _stop_child(self, proc):
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)

    def test_child_imports_parent_source_from_unrelated_cwd(self):
        out_path = self.tmp / "source-probe.txt"
        command = self._child_bind_command(f"{self.A}__topic__999", out_path)
        result = subprocess.run(command, cwd=self.tmp, capture_output=True,
                                text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(Path(str(out_path) + ".ready").read_text()),
                         self._child_source_identity()[1])
        self.assertEqual(out_path.read_text(), "1")
        self.assertEqual(ml.bindings().get(f"{self.A}__topic__999"), self.A)

    def test_child_rejects_source_hash_mismatch_before_writing(self):
        out_path = self.tmp / "source-mismatch.txt"
        command = self._child_bind_command(f"{self.A}__topic__999", out_path)
        identity = json.loads(command[-1])
        identity["memory_life"]["sha256"] = "0" * 64
        command[-1] = json.dumps(identity)
        result = subprocess.run(command, cwd=self.tmp, capture_output=True,
                                text=True, timeout=40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("child source identity mismatch", result.stderr)
        self.assertFalse(Path(str(out_path) + ".ready").exists())
        self.assertFalse(out_path.exists())
        self.assertNotIn(f"{self.A}__topic__999", ml.bindings())

    def _replace_hook_with_child(self, migrate_places, key, out_path, procs):
        """Обёртка _journal_replace: в окне writing→replace запускает НАСТОЯЩИЙ
        внешний процесс с bind_place той/другой пары и даёт ему время либо
        записать (старый мир), либо встать на общий журнал-замок (новый)."""
        real_replace = migrate_places._journal_replace
        fired = {"done": False}

        def hooked(memory_life_mod, payload):
            if not fired["done"]:
                fired["done"] = True
                proc = subprocess.Popen(self._child_bind_command(key, out_path),
                                        cwd=self.tmp)
                procs.append(proc)
                self.addCleanup(self._stop_child, proc)
                ready = Path(str(out_path) + ".ready")
                deadline = time.time() + 30
                while not ready.exists() and time.time() < deadline:
                    self.assertIsNone(proc.poll(), "child exited before source handshake")
                    time.sleep(0.05)
                self.assertTrue(ready.exists(), "child source handshake timed out")
                self.assertEqual(json.loads(ready.read_text(encoding="utf-8")),
                                 self._child_source_identity()[1])
                time.sleep(0.7)
            return real_replace(memory_life_mod, payload)

        return hooked

    def test_parallel_distinct_key_write_survives_the_replace_window(self):
        """Её REPAIR9 P0 (distinct-key) дословно: внешний ПРОЦЕСС кладёт другой
        ключ между подготовкой байтов и atomic replace — full-file замена
        мигратора не смеет его стереть: у журнала единая межпроцессная граница
        записи, которой подчиняются и bind_place, и мигратор."""
        import migrate_places
        self._two_rooms()
        out_path = self.tmp / "child-distinct.txt"
        procs = []
        hook = self._replace_hook_with_child(
            migrate_places, f"{self.A}__topic__999", out_path, procs)
        with mock.patch.object(migrate_places, "_journal_replace",
                               side_effect=hook):
            code, out = self._main("--base", str(self.tmp), "--peer", self.A)
        self.assertEqual(code, 0, out)
        for proc in procs:
            self.assertEqual(proc.wait(timeout=40), 0, "child writer failed")
        self.assertEqual(out_path.read_text(encoding="utf-8").strip(), "1",
                         "внешний процесс реально записал свой ключ")
        self.assertEqual(ml.bindings().get(f"{self.A}__topic__999"), self.A,
                         "параллельная ЧУЖАЯ привязка пережила замену журнала")

    def test_parallel_same_key_write_is_not_falsely_claimed(self):
        """Её REPAIR9 P0 (same-key) дословно: внешний ПРОЦЕСС кладёт ту же пару
        в окне writing→replace. Успешная чужая запись не смеет исчезнуть через
        ложное присвоение и recovery."""
        import migrate_places
        self._two_rooms()
        out_path = self.tmp / "child-same.txt"
        procs = []
        hook = self._replace_hook_with_child(
            migrate_places, f"{self.A}__topic__100", out_path, procs)
        with self._force_lost_events():
            with mock.patch.object(migrate_places, "_journal_replace",
                                   side_effect=hook):
                code, out = self._main("--base", str(self.tmp), "--peer", self.A)
        self.assertEqual(code, 1, out)
        for proc in procs:
            self.assertEqual(proc.wait(timeout=40), 0, "child writer failed")
        child_added = out_path.read_text(encoding="utf-8").strip()
        pair_alive = ml.bindings().get(f"{self.A}__topic__100") == self.A
        self.assertFalse(child_added == "1" and not pair_alive,
                         "чужая успешно записанная пара исчезла после recovery: "
                         "ложное присвоение авторства")

    def test_external_write_after_torn_crash_stays_and_blocks_proof(self):
        """Crash/re-entry с внешним writer'ом: после обрыва writing→done чужая
        запись (через ОБЩИЙ примитив) меняет журнал — sha-свидетель честно
        перестаёт сходиться: чужое живёт, своё остаётся недоказуемым."""
        import migrate_places
        self._two_rooms()

        real_replace = migrate_places._journal_replace

        def crash_after_replace(memory_life_mod, payload):
            real_replace(memory_life_mod, payload)
            raise OSError("обрыв сразу после замены журнала")

        with self._force_lost_events():
            with mock.patch.object(migrate_places, "_journal_replace",
                                   side_effect=crash_after_replace):
                try:
                    self._main("--base", str(self.tmp), "--peer", self.A)
                except OSError:
                    pass
        ml.bind_place(self.A, [f"{self.A}__topic__999"])   # внешний, ПОСЛЕ обрыва
        rid = str(self._runs(self.A)[0]["run_id"])
        out = migrate_places.rollback(ml, tr, only_peer=self.A, run_id=rid)
        self.assertEqual(ml.bindings().get(f"{self.A}__topic__999"), self.A,
                         "чужая пост-крашевая запись жива")
        self.assertEqual(ml.bindings().get(f"{self.A}__topic__100"), self.A,
                         "своя пара без сошедшегося свидетеля НЕ снята — не гадаем")
        self.assertEqual(out.get("bindings_unproven"), 1,
                         "недоказуемость названа вслух")

    def test_crash_before_committed_receipt_still_attributes_own_binding(self):
        """Её REPAIR8-репро дословно: пара уже записана в журнал, а
        committed-receipt не успел лечь. Доводка обязана снять СОБСТВЕННУЮ
        пару упавшего прогона по durable per-entry прогрессу — без set-diff."""
        import migrate_places
        self._two_rooms()

        real_receipt = migrate_places._write_bindings_receipt

        def crash_on_commit(run_dir, status, entries):
            if status == "committed":
                raise OSError("обрыв до committed-receipt")
            return real_receipt(run_dir, status, entries)

        with self._force_lost_events():
            with mock.patch.object(migrate_places, "_write_bindings_receipt",
                                   side_effect=crash_on_commit):
                try:
                    self._main("--base", str(self.tmp), "--peer", self.A)
                except OSError:
                    pass

        # разрыв настоящий: пара в журнале, факта нет
        self.assertEqual(ml.bindings().get(f"{self.A}__topic__100"), self.A)
        runs = self._runs(self.A)
        self.assertEqual(len(runs), 1)
        rid = str(runs[0]["run_id"])

        out = migrate_places.rollback(ml, tr, only_peer=self.A, run_id=rid)
        self.assertEqual(out["run_id"], rid, out)
        self.assertNotIn(f"{self.A}__topic__100", ml.bindings(),
                         "доводка сняла собственную, доказанно записанную пару "
                         "упавшего прогона")

    def test_torn_journal_write_is_proven_by_sha_witness(self):
        """Обрыв ровно МЕЖДУ заменой журнала и фиксацией `done`: состояние
        `writing` + sha-свидетель обязаны доказать авторство пары."""
        import migrate_places
        self._two_rooms()

        real_replace = migrate_places._journal_replace

        def crash_after_replace(memory_life_mod, payload):
            real_replace(memory_life_mod, payload)
            raise OSError("обрыв сразу после замены журнала")

        with self._force_lost_events():
            with mock.patch.object(migrate_places, "_journal_replace",
                                   side_effect=crash_after_replace):
                try:
                    self._main("--base", str(self.tmp), "--peer", self.A)
                except OSError:
                    pass

        self.assertEqual(ml.bindings().get(f"{self.A}__topic__100"), self.A,
                         "пара легла в журнал, «done» зафиксировать не успели")
        rid = str(self._runs(self.A)[0]["run_id"])
        out = migrate_places.rollback(ml, tr, only_peer=self.A, run_id=rid)
        self.assertNotIn(f"{self.A}__topic__100", ml.bindings(),
                         "sha-свидетель доказал авторство: текущий журнал — "
                         "ровно наш записанный результат")
        self.assertNotIn("bindings_unproven", out,
                         "недоказуемых записей нет: свидетель сошёлся")

    def test_generated_rollback_command_is_copy_pasteable(self):
        """Её REPAIR6 CLI-мина: run_id не смеет начинаться с «-» — иначе
        напечатанная самим инструментом команда `--run <id>` падает в argparse."""
        import migrate_places
        self._two_rooms()
        copied, rid = migrate_places.snapshot_states(ml, only_peer=self.A,
                                                     telegram_routes=tr)
        self.assertFalse(str(rid).startswith("-"),
                         f"run_id {rid!r} начинается с «-»: copy-paste "
                         f"`--run {rid}` argparse прочтёт как флаг")
        code, out = self._main("--base", str(self.tmp), "--rollback",
                               "--peer", self.A, "--run", str(rid))
        self.assertEqual(code, 0, out)
        self.assertIn("откат по manifest", out,
                      "напечатанная инструментом команда работает дословно")

    def test_an_unknown_peer_is_refused_before_any_write(self):
        self._two_rooms()
        before = self._tree_hash()
        code, out = self._main("--base", str(self.tmp), "--peer", "-100999999")
        self.assertEqual(code, 2, out)
        self.assertIn("не найден", out)
        self.assertEqual(self._tree_hash(), before)



class TestSeedTrustsOnlyProvenance(Base):
    """Посев: вердикт «форум» — только от авторитетных topic-записей.

    Исторические kind=topic писались и фантомным путём («topic #N» из вывода
    веток) — вердикт о форумности не имеет права выводиться из самой болезни.
    ⚠ FALSE-из-тишины в этом кандидате сознательно сохранён: сведение обычной
    комнаты мигратором держится на нём по построению; его запрет — вместе с
    placement-слоем evidence-ветки, и тест на это живёт там же.
    """

    def _seed(self, peer):
        import migrate_places
        return migrate_places.seed_registry(group_context, tr, dry_run=False,
                                            only_peer=str(peer))

    def test_phantom_topic_record_no_longer_seeds_true(self):
        group_context.record_topic(FORUM, 5, "topic #5", message_id=5)
        self._msg(FORUM, 5, topic=5)
        self._msg(FORUM, 6, topic=5)
        rows = self._seed(FORUM)
        self.assertEqual(tr.status_at(FORUM, 5)[0], tr.UNKNOWN,
                         "запись без провенанса не имеет права сеять НИКАКОЙ вердикт")
        self.assertEqual(rows[0]["openers"], 0)
        self.assertEqual(rows[0].get("openers_total"), 1,
                         "отчёт обязан показывать полный счёт записей")

    def test_authoritative_opener_seeds_true(self):
        group_context.record_topic(FORUM, 5, "Вход", message_id=5,
                                   origin="opener")
        self._msg(FORUM, 5, topic=5)
        self._msg(FORUM, 6, topic=5)
        rows = self._seed(FORUM)
        self.assertEqual(tr.status_at(FORUM, 5)[0], tr.TRUE)
        self.assertEqual(rows[0]["openers"], 1)

    def test_forum_list_origin_is_authoritative_too(self):
        group_context.record_topic(FORUM, 9, "Из каталога", message_id=9,
                                   origin="forum_list")
        self._msg(FORUM, 9, topic=9)
        rows = self._seed(FORUM)
        self.assertEqual(tr.status_at(FORUM, 9)[0], tr.TRUE)
        self.assertEqual(rows[0]["openers"], 1)

    def test_silence_writes_no_epoch_at_all(self):
        self._msg(ROOM, 100)
        self._msg(ROOM, 101, topic=100, reply=100)
        rows = self._seed(ROOM)
        self.assertEqual(tr.status_at(ROOM, 100)[0], tr.UNKNOWN,
                         "тишина архива породила вердикт — FALSE из тишины")
        self.assertIn("воздержание", rows[0]["verdict"])

    def test_abstention_still_writes_positive_artifact_map(self):
        self._msg(ROOM, 100)
        self._msg(ROOM, 101, topic=100, reply=100)
        self._seed(ROOM)
        self.assertIn(100, tr.artifacts_of(ROOM),
                      "placement-свидетельство (ветка с корнем) обязано выжить "
                      "при воздержании по форумности")

    def test_late_authoritative_opener_upgrades_bare_topic_row(self):
        """Её P1 25.08: поздний authoritative opener глотался dedupe-ключом
        без провенанса, и посев оставался UNKNOWN при живом свидетельстве."""
        group_context.record_topic(FORUM, 5, "Вход", message_id=5)
        group_context.record_topic(FORUM, 5, "Вход", message_id=5,
                                   origin="opener")
        self._msg(FORUM, 5, topic=5)
        self._msg(FORUM, 6, topic=5)
        rows = self._seed(FORUM)
        self.assertEqual(tr.status_at(FORUM, 5)[0], tr.TRUE,
                         "поздний authoritative opener проглочен bare-строкой")
        self.assertEqual(rows[0]["openers"], 1)
        self.assertEqual(rows[0].get("openers_total"), 2,
                         "обе строки обязаны быть видимы счёту")

    def test_replay_of_same_authoritative_row_still_dedupes(self):
        first = group_context.record_topic(FORUM, 5, "Вход", message_id=5,
                                           origin="opener")
        again = group_context.record_topic(FORUM, 5, "Вход", message_id=5,
                                           origin="opener")
        self.assertTrue(first)
        self.assertFalse(again,
                         "повтор той же authoritative записи обязан дедупиться")
        topics = [r for r in group_context.iter_records(FORUM)
                  if r.get("kind") == "topic"]
        self.assertEqual(len(topics), 1)

    def test_record_topic_keeps_origin_and_old_rows_stay_bare(self):
        group_context.record_topic(FORUM, 7, "С провенансом", message_id=7,
                                   origin="forum_list")
        group_context.record_topic(FORUM, 8, "Без провенанса", message_id=8)
        rows = [r for r in group_context.iter_records(FORUM)
                if r.get("kind") == "topic"]
        by_topic = {int(r["topic_id"]): r for r in rows}
        self.assertEqual(by_topic[7].get("origin"), "forum_list")
        self.assertNotIn("origin", by_topic[8],
                         "форма записей без провенанса меняться не должна")


if __name__ == "__main__":
    unittest.main()
