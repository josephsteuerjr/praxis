"""26.09 — бут стоял 35+ минут в поиске in_doubt, три потока хендлеров жгли ЦП на правках.

Замер прода 26.09 (py-spy с хоста, рестарт 22:37:29Z): через 35 минут раннер всё ещё был в
`recover_durable_state` → `reconcile_in_doubt_from_receipts` → `_in_doubt_run_ids`, который
брал замок и разбирал манифест у каждого из ~8,5 тыс. прогонов — «на связи» не звучало, часы
(сон, расписание, пульс, возобновление) не поднимались. Рядом три потока хендлеров стояли в
`group_context._latest_message_states`: каждое входящее сообщение группы (и правка, и
удаление) спрашивало `latest_message`, а тот разбирал всё окно архива комнаты и на каждый
правленый id перебирал весь словарь состояний.

Прибито: in_doubt ищется среди `live_run_ids` (терминальные — без замка); `latest_message`
разбирает только строки своего id, копии правленых id собираются одним проходом. Ответы те же:
сверено со старой реализацией на случайных архивах с правками, удалениями, неизвестными темами,
дрейфом маршрута, мусорными строками и чужими пирами.
"""
from __future__ import annotations

import datetime as _dt
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent
import group_context
import run_manager
from run_context import RunContext


class InDoubtScanSkipsTerminalRuns(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="praxis-boot-hot-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.manager = run_manager.RunManager(self.base)
        previous = agent._RUN_MANAGER
        self.addCleanup(lambda: setattr(agent, "_RUN_MANAGER", previous))

    def create(self, suffix: str) -> str:
        ctx = RunContext.create(run_id=f"run-{suffix}", kind="computer", goal=f"g {suffix}",
                                principal_id="telegram:1", scope="owner", origin_chat_id="1")
        self.manager.create(ctx, "# t\n")
        self.manager.transition(ctx.run_id, "running")
        return ctx.run_id

    def test_terminal_runs_are_not_locked_and_in_doubt_is_still_found(self):
        done = []
        for i in range(6):
            run_id = self.create(f"d{i}")
            self.manager.transition(run_id, "done")
            done.append(run_id)
        cancelled = self.create("c")
        self.manager.transition(cancelled, "cancelled")
        doubt = self.create("doubt")
        self.manager.start_tool(doubt, "call-send", "send", {"to": "1"}, side_effect=True)
        self.manager.transition(doubt, "in_doubt", expected="running")
        paused = self.create("paused")
        self.manager.transition(paused, "paused", expected="running")

        fresh = run_manager.RunManager(self.base)     # как после рестарта: кэшей нет
        agent._RUN_MANAGER = fresh
        locked: list[str] = []
        real = fresh._locked

        def spy(run_dir, *args, **kwargs):
            locked.append(Path(run_dir).name)
            return real(run_dir, *args, **kwargs)
        with mock.patch.object(fresh, "_locked", spy):
            found = agent._in_doubt_run_ids()
        self.assertEqual(found, [doubt])
        for run_id in done + [cancelled]:
            self.assertNotIn(run_id, locked, "терминальный прогон — без замка")
        self.assertIn(doubt, locked)
        self.assertIn(paused, locked, "нетерминальный — прежним путём под замком")


# --------------------------------------------------------------------------- #
#  Эталон: `_latest_message_states` и `latest_message` ДО 26.09, дословно.
# --------------------------------------------------------------------------- #
def _reference_states(peer_id):
    latest = {}
    unknown_deletions = {}
    for index, row in enumerate(group_context.iter_records(peer_id)):
        if row.get("kind") not in ("message", "deletion"):
            continue
        mid = int(row.get("message_id") or 0)
        if not mid:
            continue
        topic = row.get("topic_id")
        key = (topic, mid)
        rank = group_context._state_rank(row, index)
        if row.get("kind") == "deletion":
            if topic is None and not row.get("original_known"):
                previous_unknown = unknown_deletions.get(mid)
                if previous_unknown is None or rank >= previous_unknown[0]:
                    unknown_deletions[mid] = (rank, row)
            previous = latest.get(key)
            if previous is None or rank >= previous[0]:
                latest[key] = (rank, row)
            continue
        unknown = unknown_deletions.get(mid)
        if unknown is not None and unknown[0] >= rank:
            merged = group_context._merged_unknown_deletion(unknown[1], row)
            latest.pop((None, mid), None)
            previous = latest.get(key)
            if previous is None or unknown[0] >= previous[0]:
                latest[key] = (unknown[0], merged)
            unknown_deletions.pop(mid, None)
            continue
        previous = latest.get(key)
        if previous is None or rank >= previous[0]:
            latest[key] = (rank, row)
        if unknown is not None:
            unknown_deletions.pop(mid, None)
    edited_messages = {
        mid for (_topic, mid), (_rank, row) in latest.items() if row.get("edited_at")
    }
    for mid in edited_messages:
        candidates = [(key, row) for key, (_rank, row) in latest.items() if key[1] == mid]
        if len(candidates) < 2:
            continue
        winner_key = max(candidates, key=lambda pair: latest[pair[0]][0])[0]
        for key, _row in candidates:
            if key != winner_key:
                latest.pop(key, None)
    return {key: row for key, (_rank, row) in latest.items()}


def _reference_latest(peer_id, message_id):
    wanted = int(message_id)
    candidates = [row for (_topic, mid), row in _reference_states(str(peer_id)).items()
                  if mid == wanted]
    for row in reversed(candidates):
        if row.get("kind") == "deletion":
            return dict(row)
    return dict(candidates[-1]) if candidates else None


PEER = "-1002345"
# Id, чьи цифры сидят внутри других id, меток времени и текста: фильтр по цифрам — надмножество.
MIDS = [1, 2, 5, 7, 12, 15, 20, 25, 50, 51, 105, 150, 205, 2026, 2345, 4242]
TOPICS = [None, 3, 7, 51]


def _stamp(rng: random.Random) -> str:
    base = _dt.datetime(2026, 9, 25, 20, 0, tzinfo=_dt.timezone.utc)
    # Узкий разброс: правки одной секунды и равные метки случаются часто.
    return (base + _dt.timedelta(seconds=rng.randint(0, 40))).isoformat().replace("+00:00", "Z")


def _message(rng, mid, topic, *, edited=False):
    return {
        "schema": group_context.SCHEMA_MESSAGE, "kind": "message", "peer_id": PEER,
        "topic_id": topic, "topic_title": "" if topic is None else f"тема {topic}",
        "message_id": mid, "sender_id": rng.choice([11, 12, -1005]),
        "sender_name": rng.choice(["Аня", "Боря 15", "бот"]),
        "reply_to_message_id": rng.choice([None, 5, 50]),
        "timestamp": _stamp(rng), "edited_at": _stamp(rng) if edited else None,
        "revision_order": rng.randint(0, 2), "text": f"текст {rng.randint(0, 3000)} про {mid}",
        "media": "", "outgoing": rng.random() < 0.2,
    }


def _deletion(rng, mid, topic, *, known):
    return {
        "schema": group_context.SCHEMA_DELETION, "kind": "deletion", "peer_id": PEER,
        "topic_id": topic, "topic_title": "", "message_id": mid,
        "sender_id": None, "sender_name": "", "reply_to_message_id": None,
        "timestamp": _stamp(rng), "deleted_at": _stamp(rng),
        "original_known": known, "outgoing": False,
    }


def _archive_lines(seed: int, count: int) -> list[str]:
    rng = random.Random(seed)
    lines = []
    for _ in range(count):
        mid = rng.choice(MIDS)
        roll = rng.random()
        if roll < 0.40:
            row = _message(rng, mid, rng.choice(TOPICS))
        elif roll < 0.70:
            row = _message(rng, mid, rng.choice(TOPICS), edited=True)     # правка, дрейф темы
        elif roll < 0.80:
            row = _deletion(rng, mid, rng.choice(TOPICS[1:]), known=True)
        elif roll < 0.90:
            row = _deletion(rng, mid, None, known=False)                  # тема неизвестна
        elif roll < 0.94:
            row = {"schema": group_context.SCHEMA_TOPIC, "kind": "topic", "peer_id": PEER,
                   "topic_id": rng.choice(TOPICS[1:]), "title": f"т {mid}",
                   "timestamp": _stamp(rng), "message_id": mid}
        elif roll < 0.97:
            row = dict(_message(rng, mid, None), peer_id="-1009999")      # чужой пир
        else:
            lines.append('{"kind":"message","message_id":%d,' % mid)      # рваная строка
            continue
        lines.append(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return lines


class LatestMessageReadsOnlyItsOwnRows(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="praxis-gcx-")
        self.addCleanup(tmp.cleanup)
        for patcher in (mock.patch.object(group_context, "GROUPS_DIR", Path(tmp.name) / "groups"),
                        mock.patch.object(group_context, "STATE_DIR", Path(tmp.name) / "state"),
                        mock.patch.object(group_context, "_KEY_CACHE", {})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, lines: list[str]) -> None:
        path = group_context.archive_path(PEER)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    def test_answers_match_the_previous_implementation(self):
        for seed in range(12):
            self.write(_archive_lines(seed, 260))
            with self.subTest(seed=seed):
                self.assertEqual(group_context._latest_message_states(PEER),
                                 _reference_states(PEER))
                for mid in MIDS + [3, 999]:
                    self.assertEqual(group_context.latest_message(PEER, mid),
                                     _reference_latest(PEER, mid), f"id {mid}")

    def test_only_lines_with_the_id_digits_are_parsed(self):
        lines = _archive_lines(7, 400)
        self.write(lines)
        real_loads = json.loads
        with mock.patch.object(group_context.json, "loads", side_effect=real_loads) as loads:
            group_context.latest_message(PEER, 4242)
        candidates = sum(1 for line in lines if "4242" in line)
        self.assertLessEqual(loads.call_count, candidates)
        self.assertLess(loads.call_count, len(lines) // 4)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
