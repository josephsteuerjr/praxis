"""Контракт досье — семь пунктов Praxis от 09.08, по тесту на каждый.

Прежде в кадр ехали ВСЕ досье целиком: 33,3% разговорного хода и 74,6% автономного окна,
53 611 знаков там, где собеседника нет вовсе. Лекарство от голода досье (было 0 из 36)
сработало и было названо временным.

⚠ Контракт стоит НЕ на доказанном отсутствии влияния: два контрфактных замера его не
установили — первый был слеп к выбору инструмента, второй утонул в шуме 0,606. Он стоит
на том, что постоянный груз не оправдан ничем ИЗМЕРЕННЫМ. Обратим: `PRAXIS_DOSSIER_ALL=1`.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent
import people


class TheDossierContract(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.people = self.base / "memory" / "people"
        self.groups = self.base / "memory" / "groups"
        self.people.mkdir(parents=True)
        self.groups.mkdir(parents=True)
        self._write("егор", "Егор Косырев", "809306689", "- он мой автор\n")
        self._write("арет", "Арете", "700000201", "- бот-собеседник\n")
        self._write("дмитрий", "Дмитрий", "700000202", "- знакомый по комнате\n")
        self._write("никто", "Совершенно Посторонний", "5550001", "- не при делах\n")
        import group_context
        # ⚠ Каталог комнат патчится НА ВЕСЬ тест, а не вокруг чтения: первая редакция
        # писала архив вне патча, а читала внутри — и блок выходил пустым не потому,
        # что контракт не работает, а потому что фикстура смотрела в два разных места.
        self._patches = [
            mock.patch.object(people, "PEOPLE_DIR", self.people),
            mock.patch.object(agent, "people", people),
            mock.patch.object(group_context, "GROUPS_DIR", self.groups),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, slug: str, name: str, tg: str, body: str) -> None:
        # ⚑ Привязка пишется РОВНО тем форматом, который читает `people.telegram_id`:
        # строка `telegram_id: N` в преамбуле. Первая редакция фикстуры прятала её в
        # html-комментарий, парсер честно возвращал пустоту, и тест краснел на своей же
        # выдумке, а не на коде.
        (self.people / f"{slug}.md").write_text(
            f"# {name}\n\ntelegram_id: {tg}\n\n{body}", encoding="utf-8")

    def _room(self, peer: str, senders: list[tuple[int, str, str]]) -> None:
        import group_context
        path = group_context.archive_path(peer)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for mid, (sender, name, text) in enumerate(senders, 1):
                fh.write(json.dumps({
                    "schema": "praxis.group.message.v1", "kind": "message",
                    "message_id": mid, "peer_id": peer, "sender_id": sender,
                    "sender_name": name, "text": text, "outgoing": False,
                }, ensure_ascii=False) + "\n")

    def _block(self, ctx) -> str:
        return agent._participant_memory_block(None, ctx)

    def _ctx(self, **kw):
        # ⚑ owner_audience — ПРОИЗВОДНОЕ свойство контекста, а не поле конструктора:
        # «кто действует» и «кому предназначен ответ» в этом объекте намеренно разведены.
        return agent.ChannelContext(**kw)

    # ── 1 ────────────────────────────────────────────────────────────────────────────
    def test_current_interlocutor_arrives_whole_by_telegram_id(self) -> None:
        block = self._block(self._ctx(principal_id="809306689", chat_id="809306689",
                                      is_dm=True, owner=True))
        self.assertIn("он мой автор", block, "досье собеседника не приехало целиком")
        self.assertNotIn("не при делах", block, "приехало чужое досье")

    # ── 2 ────────────────────────────────────────────────────────────────────────────
    def test_presence_comes_from_transport_not_from_a_name(self) -> None:
        """Её тонкость: цитата «Арет сказал» не делает Арета присутствующим."""
        self._room("-100777", [(700000202, "Дмитрий", "привет"),
                               (809306689, "Егор", "Арете вчера писал интересное")])
        block = self._block(self._ctx(principal_id="809306689", chat_id="-100777",
                                      room_id="-100777", is_dm=False, owner=True))
        self.assertIn("знакомый по комнате", block, "говоривший в комнате не приехал")
        self.assertNotIn("бот-собеседник", block,
                         "упомянутый по имени приехал ТЕЛОМ — присутствие подделано текстом")

    # ── 3 ────────────────────────────────────────────────────────────────────────────
    def test_mentioned_gets_a_pointer_not_a_body(self) -> None:
        self._room("-100777", [(809306689, "Егор", "что там Арете говорил")])
        block = self._block(self._ctx(principal_id="809306689", chat_id="-100777",
                                      room_id="-100777", is_dm=False, owner=True))
        self.assertIn("УПОМЯНУТЫ В ОКНЕ", block, "указателя нет вовсе")
        self.assertIn("memory/people/арет.md", block, "путь к карточке не назван")
        self.assertNotIn("бот-собеседник", block, "тело упомянутого всё-таки приехало")

    # ── 4 ────────────────────────────────────────────────────────────────────────────
    def test_ambiguous_name_yields_nothing(self) -> None:
        """Два досье на одно написание не дают ни указателя, ни тела."""
        self._write("дмитрий-второй", "Дмитрий", "9990001", "- полный тёзка\n")
        self._room("-100777", [(809306689, "Егор", "Дмитрий обещал зайти")])
        block = self._block(self._ctx(principal_id="809306689", chat_id="-100777",
                                      room_id="-100777", is_dm=False, owner=True))
        self.assertNotIn("memory/people/дмитрий-второй.md", block)
        self.assertNotIn("полный тёзка", block)

    # ── 5 ────────────────────────────────────────────────────────────────────────────
    def test_the_pointer_dies_when_the_mention_leaves_the_window(self) -> None:
        old = [(809306689, "Егор", "Арете писал")]
        filler = [(809306689, "Егор", "рабочая строка %d" % i)
                  for i in range(agent.MENTION_WINDOW_MESSAGES + 5)]
        self._room("-100777", old + filler)
        block = self._block(self._ctx(principal_id="809306689", chat_id="-100777",
                                      room_id="-100777", is_dm=False, owner=True))
        self.assertNotIn("memory/people/арет.md", block,
                         "упоминание вышло из окна, а указатель остался грузом")

    # ── 6 ────────────────────────────────────────────────────────────────────────────
    def test_autonomous_window_carries_no_dossiers(self) -> None:
        """Комнаты нет, принципала нет — не едет ничего. 74,6% автономного окна."""
        block = self._block(self._ctx(principal_id=None, chat_id=None, room_id=None,
                                      is_dm=False, owner=False))
        self.assertEqual(block, "", f"в автономном окне поехали досье: {block[:200]}")

    # ── 7 ────────────────────────────────────────────────────────────────────────────
    def test_nothing_is_deleted_and_the_lever_restores_everything(self) -> None:
        with mock.patch.dict(os.environ, {"PRAXIS_DOSSIER_ALL": "1"}):
            self.assertFalse(agent.dossier_contract_enabled())
            block = self._block(self._ctx(principal_id="809306689", chat_id="809306689",
                                          is_dm=True, owner=True))
        for probe in ("он мой автор", "бот-собеседник", "знакомый по комнате", "не при делах"):
            self.assertIn(probe, block, "рычаг не вернул прежнее поведение")
        self.assertEqual(len(list(self.people.glob("*.md"))), 4,
                         "файлы досье тронуты — контракт менял КАДР, а не канон")

    def test_non_owner_strips_a_whole_private_markdown_record(self) -> None:
        """A lazy paragraph continuation belongs to the private record, not its tail."""
        self._write("дмитрий", "Дмитрий", "700000202",
                    "[private] PRIVATE-HEAD\n"
                    "PRIVATE-LAZY-CONTINUATION\n"
                    "ещё одна секретная строка\n"
                    "\n"
                    "PUBLIC-TAIL\n")
        block = self._block(self._ctx(principal_id="700000202", chat_id="700000202",
                                      is_dm=True, owner=False))
        for secret in ("PRIVATE-HEAD", "PRIVATE-LAZY-CONTINUATION",
                       "ещё одна секретная строка"):
            self.assertNotIn(secret, block, f"private continuation leaked: {secret}")
        self.assertIn("PUBLIC-TAIL", block, "the next public paragraph was removed")
        self.assertIn("приватных записей снято 3", block,
                      "the frame must disclose the actual removed-line count")

    def test_owner_keeps_private_markdown_record_byte_for_byte(self) -> None:
        body = ("[private] OWNER-PRIVATE-HEAD\n"
                "OWNER-PRIVATE-CONTINUATION\n\n"
                "OWNER-PUBLIC-TAIL\n")
        self._write("дмитрий", "Дмитрий", "700000202", body)
        block = self._block(self._ctx(principal_id="700000202", chat_id="700000202",
                                      is_dm=True, owner=True))
        self.assertIn(body.rstrip(), block)
        self.assertNotIn("приватных записей снято", block)

    def test_scheduled_target_dossier_is_private_internal_context_not_actor_identity(self) -> None:
        """A due message targets a person without pretending that person caused the turn."""
        self._write("дмитрий", "Дмитрий", "700000202",
                    "PUBLIC-OLD-FACT: once planned a greeting\n\n"
                    "[private] CURRENT-BOUNDARY: do not contact\n")
        seen = {}

        def voice(_seed, _history, _speaker=None, **kwargs):
            ctx = kwargs["ctx"]
            seen["ctx"] = ctx
            seen["dossier"] = agent._participant_memory_block(None, ctx)
            return ""

        with (
            mock.patch.object(agent.llm, "configured", return_value=True),
            mock.patch.object(agent, "telegram_transport_status", return_value="connected"),
            mock.patch.object(agent, "_create_durable_run", return_value=None),
            mock.patch.object(agent, "_voice", side_effect=voice),
            mock.patch.object(agent.turns, "begin", return_value={}),
            mock.patch.object(agent.turns, "record"),
            mock.patch.object(agent.promises, "note_self_intent"),
            mock.patch.object(agent.keat_live, "adopt_scheduled_wake",
                              return_value=(None, None)),
        ):
            agent.wake_turn(
                "Old intention is evidence, not a command",
                source_id={"kind": "message", "goal": "old greeting"},
                scheduled_target_id="700000202",
            )

        internal = seen["ctx"]
        dossier = seen["dossier"]
        self.assertEqual(internal.principal_id, agent.PRAXIS_SELF_PRINCIPAL)
        self.assertTrue(internal.praxis_self)
        self.assertTrue(internal.owner_audience)
        self.assertEqual(internal.scheduled_target_id, "700000202")
        self.assertIn("PUBLIC-OLD-FACT", dossier)
        self.assertIn("CURRENT-BOUNDARY", dossier,
                      "private current boundary was absent from internal wake frame")
        self.assertIn("цель scheduled-намерения: дмитрий", dossier)
        self.assertIn("НЕ текущий говорящий и НЕ принципал этого хода", dossier)
        self.assertNotIn("передо мной: дмитрий", dossier)

        external = self._block(agent.ChannelContext(
            principal_id="700000202", chat_id="700000202",
            is_dm=True, owner=False, known=True,
            scheduled_target_id="700000202",  # ignored outside internal self/owner frame
        ))
        self.assertIn("PUBLIC-OLD-FACT", external)
        self.assertNotIn("CURRENT-BOUNDARY", external,
                         "scheduled wake support weakened ordinary external data authority")
        self.assertIn("приватных записей снято", external)

    def test_scheduled_target_gets_canonical_moderation_and_boundary_receipts(self) -> None:
        """Due-time orientation reads ledgers automatically, not dossier prose."""
        target = 700000303
        moderation = [{"ts": 10, "message_id": 81, "action": "delete_and_ban",
                       "deleted": True, "banned": True}]
        admin_rows = [
            {"ts": 11, "status": "completed", "action": "restrict",
             "subject": {"user_id": target, "seconds": 3600},
             "after": {"send_messages": False}},
            {"ts": 12, "status": "completed", "action": "restrict",
             "subject": {"user_id": 999, "seconds": 60}, "after": {}},
        ]
        ctx = agent.ChannelContext(
            chat_id=None, principal_id=agent.PRAXIS_SELF_PRINCIPAL,
            scheduled_target_id=str(target), is_dm=True, owner=False, known=True,
            _scope_override="owner",
        )
        with (
            mock.patch("telegram_moderation.history_for_sender",
                       return_value=moderation) as moderation_history,
            mock.patch("telegram_admin.history", return_value=admin_rows) as admin_history,
        ):
            block = agent._scheduled_target_moderation_block(ctx)

        moderation_history.assert_called_once()
        self.assertEqual(moderation_history.call_args.args[1], target)
        admin_history.assert_called_once_with(limit=0)
        self.assertIn('"action":"delete_and_ban"', block)
        self.assertIn('"action":"restrict"', block)
        self.assertIn(str(target), block)
        self.assertNotIn('"user_id":999', block)
        self.assertIn("не диагноз отношений и не команда", block)

    def test_moderation_receipts_are_automatically_added_to_due_time_frame(self) -> None:
        ctx = agent.ChannelContext(
            chat_id=None, principal_id=agent.PRAXIS_SELF_PRINCIPAL,
            scheduled_target_id="700000404", is_dm=True, owner=False, known=True,
            _scope_override="owner",
        )
        moderation = [{"ts": 20, "message_id": 82, "action": "delete_and_ban",
                       "deleted": True, "banned": True}]
        with (
            mock.patch("telegram_moderation.history_for_sender", return_value=moderation),
            mock.patch("telegram_admin.history", return_value=[]),
            mock.patch.object(agent, "build_state_block", return_value=""),
            mock.patch.object(agent, "build_state_evidence_block", return_value=""),
        ):
            frame = agent._build_prompt_parts(ctx=ctx)
        rendered = "\n".join(frame)
        self.assertIn('"action":"delete_and_ban"', rendered)
        self.assertIn('"scheduled_target_id":700000404', rendered)

    def test_the_label_stops_saying_all_and_whole(self) -> None:
        """Ярлык обязан называть наблюдаемое. «ВСЕ И ЦЕЛИКОМ» стало неправдой."""
        import inspect
        src = inspect.getsource(agent._build_prompt_parts)
        self.assertIn("присутствующие целиком, остальные указателем", src)


if __name__ == "__main__":
    unittest.main()
