"""Offline regressions executing real runner functions without booting the app/tree.

AST extraction avoids runner startup side effects; only collaborators are faked.
RUNNER_BOUNDARY_SOURCE can point at the preserved original for the red baseline.
"""
import ast
from contextlib import contextmanager
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch


SOURCE = Path(os.environ.get("RUNNER_BOUNDARY_SOURCE", Path(__file__).resolve().parents[2] / "localharness/runner.py"))


def load_functions():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
    names = {"boundary_word", "_turn_in_window", "handle_bot"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == names
    env = {"STREAM": "desk", "WORD": "word", "SILENCE": "silence", "BLANK": "blank",
           "_OUTCOME_PREFIXES": ("done", "wait", "blocked"), "sys": sys, "__name__": __name__,
           "telegram_inputs": NS(select=lambda runner, chat: [{"record": {"is_dm": True, "title": "room", "sender_id": "owner", "row": {"sender_name": "Owner"}}}], activation=permitted)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), env)
    return env


@contextmanager
def permitted(*args):
    yield True


class BoundaryAudit(unittest.TestCase):
    def setUp(self):
        self.env = load_functions()

    def test_internal_notes_never_become_words(self):
        notes = ["", "ordinary private service note", "Спасибо", "1234", "done", "wait", "blocked"]
        for prefix in ("done", "wait", "blocked"):
            notes.extend([f"{prefix}: private service note", f"{prefix}: {prefix}: private service note",
                          f"{prefix}: done: wait: blocked: private service note"])
        for note in notes:
            for trace in ([], ["computer", "shell"], [{"name": "end_turn"}], ["...truncated..."]):
                with self.subTest(note=note, trace=trace):
                    self.assertEqual(self.env["boundary_word"]({"held": "unspoken", "note": note,
                                                               "tools": trace}), ("blank", ""))

    def test_voice_silence_and_other_rows(self):
        self.assertEqual(self.env["boundary_word"]({"held": "voice", "why": "  deliberate\n silence ",
                                                    "note": "private service note"}),
                         ("silence", "deliberate silence"))
        for held in (None, "", "reply", "unknown"):
            self.assertEqual(self.env["boundary_word"]({"held": held, "note": "private service note"}),
                             ("blank", ""))

    def exercise_caller(self, channel, row, text="", reply="", birth=False):
        deliveries = []
        closed = []
        sent = [reply] if reply else []
        desk = NS(title="desk", sent=[], lines=lambda n: [],
                  deliver=lambda text, **kw: deliveries.append((text, kw)))
        bot = NS(owner_id="owner", sent_now=[],
                 rooms=NS(meta=lambda c: {"is_dm": True}, lines=lambda c, n: []),
                 wake_sender=lambda c: ("Owner", "owner"), is_allowed=lambda s: True,
                 take_priorities=lambda c: [], restore_priorities=lambda *a: None,
                 deliver_text=lambda c, text: deliveries.append((text, {})))
        envelope = NS(text=text, run_id="run", outbound=(), failed=False, deferred=False)

        def run_turn(*args, **kwargs):
            # Real reply hand already delivered before the envelope returns.
            desk.sent.extend(sent)
            bot.sent_now.extend(("desk", t) for t in sent)
            return envelope

        noop = lambda *a, **kw: None
        self.env.update(_room=lambda r: desk, _conversation=lambda *a: "incoming",
                        _last_n=lambda r: 10, _agent=NS(ChannelContext=lambda **kw: NS(**kw)),
                        _deliver_unspoken=True, _set_busy=noop, _run_turn=run_turn,
                        _deliver_outbound=lambda *a: 0, _turn_record=lambda r: row,
                        _close_run=lambda *a, **kw: closed.append(kw), _compact=noop,
                        _agent_name="Agent", _bot=bot, _room_title=lambda c: "room",
                        _telegram_media_refs=lambda *a: ([], []), _status_message=False,
                        time=NS(time=lambda: 1.0), log=NS(info=noop, warning=noop, exception=noop))
        modules = {"control_watch": NS(delivery=permitted),
                   "addressing": NS(priority_banner=lambda *a: ""),
                   "turn_pulse": NS(TurnPulse=lambda *a, **kw: NS(start=noop, stop=noop))}
        with patch.dict(sys.modules, modules):
            if channel == "window":
                self.env["_turn_in_window"]("source", speaker="Owner", birth=birth)
            else:
                self.env["handle_bot"]("desk")
        return deliveries, closed

    def test_callers_do_not_deliver_internal_notes_even_with_legacy_opt_in(self):
        for channel in ("window", "telegram"):
            for birth in (False, True) if channel == "window" else (False,):
                with self.subTest(channel=channel, birth=birth):
                    delivered, closed = self.exercise_caller(channel, {"held": "unspoken",
                        "note": "wait: done: internal service note", "tools": ["...truncated..."]}, birth=birth)
                    self.assertFalse(any("internal service note" in t for t, _ in delivered))
                    self.assertTrue(all(kw.get("system") for _, kw in delivered))
                    self.assertEqual(closed[0]["delivered_text"], "")

    def test_explicit_envelope_and_reply_hand_unaffected(self):
        for channel in ("window", "telegram"):
            for hand in (False, True):
                with self.subTest(channel=channel, hand=hand):
                    delivered, closed = self.exercise_caller(channel,
                        {"held": "unspoken", "note": "private service note"},
                        text="" if hand else "explicit answer", reply="explicit answer" if hand else "")
                    self.assertEqual([t for t, _ in delivered], [] if hand else ["explicit answer"])
                    self.assertEqual(closed[0]["spoken_by_hand"], int(hand))
                    self.assertEqual(closed[0]["delivered_text"], "" if hand else "explicit answer")

    def test_held_voice_retains_silence_not_outbound_speech(self):
        for channel in ("window", "telegram"):
            delivered, closed = self.exercise_caller(channel, {"held": "voice", "why": "chosen silence",
                                                               "note": "private service note"})
            self.assertEqual(closed[0]["delivered_text"], "")
            if channel == "window":
                self.assertEqual(len(delivered), 1)
                self.assertEqual(delivered[0][1]["kind"], "silence")
                self.assertTrue(delivered[0][1]["system"])
            else:
                self.assertEqual(delivered, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
