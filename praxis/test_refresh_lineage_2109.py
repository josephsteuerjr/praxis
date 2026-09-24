"""Приём текущей ревизии сверяет ПИР родословной, а не префикс готовой строки.

Правка 08497113 (21.09) сняла пробку мельницы: ревизия, приехавшая маршрутом ветки
(`…__topic__N`), принимается группой места, потому что её lineage-ключ называет тот же
пир. Написан приём был как `logical_id.startswith(f"telegram:{chat}:")` — иммунитет на
это и предупредил. Префикс завязан на формат ключа, который складывается в другом месте:
поменяется формат — приём молча перестанет ловить веточные записи, группа снова не
погаснет, мельница снова закрутится, то есть вернётся ровно та поломка, которую чинили.
И вдобавок префикс крадёт чужую строку, когда ключ места оказывается началом другого
пира (место `A`, пир `A:B`).

Закрепляется поведение, а не текст ключа: ключ и пир отдаёт одна функция
(`_refresh_logical_target`), приём сверяет пир (`_refresh_current_targets`).

Запуск:  python praxis_test.py test_refresh_lineage_2109 -v
"""

from __future__ import annotations

import unittest

import memory_life as ml

ROOM = "-1003701205730"
BRANCH = f"{ROOM}__topic__947"


def row(chat_id: str, *, source_id: str = "34361:delete", ident: str = "evt-x",
        source: str = "telegram") -> dict:
    return {
        "id": ident,
        "kind": "conversation_message",
        "source": source,
        "source_id": source_id,
        "chat_id": chat_id,
        "ts": "2026-09-07T15:48:57.000Z",
        "dedupe_key": f"telegram:{chat_id}:34361:in",
    }


class TheCurrentRevisionIsTakenByItsPeer(unittest.TestCase):
    def setUp(self) -> None:
        self._bindings = ml.bindings
        self.places: dict[str, str] = {}
        ml.bindings = lambda: dict(self.places)

    def tearDown(self) -> None:
        ml.bindings = self._bindings

    def _targets(self, rows: dict[str, dict], chat: str = ROOM) -> dict[str, dict]:
        return ml._refresh_current_targets(rows, list(rows), chat)

    def test_a_revision_routed_through_the_branch_belongs_to_the_place(self):
        """Тот самый случай 34361: удаление записано под топиком, цель — корневая."""
        got = self._targets({"evt-1": row(BRANCH, ident="evt-1")})
        self.assertEqual(list(got), [f"telegram:{ROOM}:34361"])

    def test_a_revision_written_under_the_root_still_belongs(self):
        got = self._targets({"evt-1": row(ROOM, ident="evt-1")})
        self.assertEqual(list(got), [f"telegram:{ROOM}:34361"])

    def test_another_peer_is_not_ours(self):
        got = self._targets({"evt-1": row("-1009999999999", ident="evt-1")})
        self.assertEqual(got, {})

    def test_a_peer_that_merely_starts_with_our_key_is_not_stolen(self):
        """Префиксная проверка забирала `A:B` в место `A`; сверка пира — нет."""
        rows = {"evt-1": row("A:B", ident="evt-1")}
        self.assertTrue(ml._refresh_logical_id(rows["evt-1"]).startswith("telegram:A:"))
        self.assertEqual(ml._refresh_current_targets(rows, list(rows), "A"), {})

    def test_an_event_without_telegram_lineage_falls_back_to_chat_id(self):
        plain = row(ROOM, ident="evt-1", source="mail", source_id="letter-1")
        got = self._targets({"evt-1": plain})
        self.assertEqual(list(got), ["event:evt-1"])

    def test_the_fallback_still_reads_the_binding_journal(self):
        """Привязанная ветка остаётся нашей и для событий без родословной."""
        self.places = {BRANCH: ROOM}
        plain = row(BRANCH, ident="evt-1", source="mail", source_id="letter-1")
        self.assertEqual(list(self._targets({"evt-1": plain})), ["event:evt-1"])
        self.places = {}
        self.assertEqual(self._targets({"evt-1": plain}), {})

    def test_rows_that_are_not_conversation_messages_are_skipped(self):
        other = dict(row(ROOM, ident="evt-1"), kind="tool_call")
        self.assertEqual(self._targets({"evt-1": other}), {})

    def test_the_key_and_the_peer_come_from_one_place(self):
        """Формат ключа можно менять — пока пара остаётся согласованной."""
        sample = row(BRANCH, ident="evt-1")
        key, peer = ml._refresh_logical_target(sample)
        self.assertEqual(key, ml._refresh_logical_id(sample))
        self.assertEqual(peer, ROOM)
        self.assertTrue(key.endswith(":34361"))
        self.assertEqual(ml._refresh_logical_target({"id": "evt-2"}), ("event:evt-2", ""))


if __name__ == "__main__":
    unittest.main()
