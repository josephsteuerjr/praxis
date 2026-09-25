# -*- coding: utf-8 -*-
"""КЕАТ сквозь настоящее дерево: харнесс + дерево + подменённая модель (1.0.0).

Обещание владельцу, проверенное целиком, а не по швам: при владельце в Telegram
  * `boot.env_knobs` поднимает КЕАТ, `keat_readiness` говорит `serve_ready`;
  * `botapi.Rooms.record` захватывает обе стороны лички, `runner._dialogue` отдаёт сайдкар;
  * ход дерева (`voice_turn_envelope`) обслуживается у границы модели: в расписке
    `model_input` — `keat.served: true`, в модель уходит ВСЯ лента (история не выпадает),
    стадия `canary` — доли секунды, эпоха лежит на диске.
Модель подменена (`llm.chat`), сети нет. Дерево — HELENE_TREE_SRC, дерево сборки
(HELENE_BODY_DIR/tree) или рабочая копия рядом с репозиторием; нет дерева — пропуск.

Запуск:  python tests/t_keat_live_tree.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def tree_source() -> Path | None:
    for var, sub in (("HELENE_TREE_SRC", ""), ("HELENE_BODY_DIR", "tree")):
        raw = os.environ.get(var)
        if raw:
            candidate = Path(raw) / sub if sub else Path(raw)
            if (candidate / "keat_live.py").is_file():
                return candidate
    for name in ("port-2409", "live-fix"):
        candidate = ROOT.parent / name
        if (candidate / "keat_live.py").is_file():
            return candidate
    return None


SCRIPT = textwrap.dedent(r'''
    import json, os, sys, tempfile
    from pathlib import Path
    from unittest import mock
    desk, code = Path(sys.argv[1]), Path(sys.argv[2])
    sys.path[:0] = [str(desk), str(desk / "localharness")]
    tmp = tempfile.TemporaryDirectory()
    # Настоящий путь: на macOS временные папки лежат под /var → /private/var, а её
    # run_manager отказывает корню со ссылкой в пути (1.0.1, Mac-прогон 36196933373).
    tree = Path(tmp.name).resolve() / "data"; tree.mkdir()
    import boot
    cfg = {"telegram": {"owner_id": "4242"}, "agent": {"name": "Hélène"}, "owner": {"name": "Егор"}}
    os.environ.update(boot.env_knobs(cfg, tree=tree))
    os.environ["PRAXIS_BASE"] = str(tree)
    sys.path.insert(0, str(code))
    import sitecustomize  # noqa: F401
    import agent, memory_life, keat_readiness, llm, botapi, runner
    out = {"ready": keat_readiness.receipt()["serve_ready"]}
    runner._life, runner._agent = memory_life, agent
    rooms = botapi.Rooms(tree, memory_life, "Hélène")
    rooms.describe("4242", title="Егор", is_dm=True, sender=("Егор", "4242"))
    for mid, outgoing, text in [(10, False, "Привет, как отчёт?"), (11, True, "Почти готов."),
                                (12, False, "Покажи таблицу."), (13, True, "К вечеру пришлю."),
                                (14, False, "А тесты?")]:
        rooms.record("4242", text, outgoing=outgoing, sender=("Hélène" if outgoing else "Егор"),
                     source_id=str(mid))
    sidecar = {}
    history, current = runner._dialogue("4242", sidecar)
    out["history"], out["sidecar"] = len(history), {k: len(v) for k, v in sidecar.items()}
    voice = []
    def chat(role, *, system=None, messages, tools=None, **kw):
        if role != "voice":
            return llm.LLMResponse(text="ok", usage={"in": 1, "out": 1})
        voice.append([m["role"] for m in messages])
        if len(voice) == 1:
            return llm.LLMResponse(stop_reason="tool_use", usage={"in": 900, "out": 9},
                blocks=[{"type": "tool_use", "id": "t1", "name": "reply", "input": {"text": "Зелёные."}}])
        return llm.LLMResponse(stop_reason="tool_use", usage={"in": 9, "out": 3, "cache_read": 900},
            blocks=[{"type": "tool_use", "id": "t2", "name": "end_turn",
                     "input": {"outcome": "done", "note": "ответил"}}])
    def reply(chat_id, text, reply_to=""):
        rooms.record(str(chat_id), text, outgoing=True, sender="Hélène", source_id="15")
        return "delivered"
    ctx = agent.ChannelContext(chat_id="4242", is_dm=True, owner=True, principal_id="4242",
                               known=True, addressed=True, title="Егор")
    with mock.patch.object(llm, "chat", side_effect=chat), \
         mock.patch.object(llm, "configured", return_value=True), \
         mock.patch.dict(agent._TELETHON, {"reply": reply}):
        envelope = agent.voice_turn_envelope("4242", "\n".join(rooms.lines("4242", 80)), "Егор",
            ctx=ctx, history=history, current_text=current, occurrence_sidecar=sidecar)
    out["first_tape"] = len(voice[0]) if voice else 0
    receipts, canary = [], []
    for path in Path(agent._runs().root).rglob("events.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if row.get("kind") == "model_input" and isinstance(meta.get("keat"), dict):
                receipts.append(meta["keat"].get("served"))
            if row.get("kind") == "model_preparation_timing":
                canary.append((row.get("stages_ms") or {}).get("canary"))
    out["served"], out["canary_ms"] = receipts, canary
    out["epochs"] = len(list((tree / "memory" / ".state" / "keat" / "epochs").glob("*")))
    print("KEAT-E2E " + json.dumps(out))
''')


@unittest.skipIf(tree_source() is None, "дерева с КЕАТ рядом нет (HELENE_TREE_SRC) — сквозь дерево не проверено")
class ThroughTheTree(unittest.TestCase):
    def test_owner_stream_is_served_with_the_whole_dialogue(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "e2e.py"
            script.write_text(SCRIPT, encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if not k.startswith("PRAXIS_")}
            env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
            proc = subprocess.run([sys.executable, str(script), str(HERE.parent), str(tree_source())],
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=300, env=env, cwd=tmp)
        line = next((l for l in proc.stdout.splitlines() if l.startswith("KEAT-E2E ")), "")
        self.assertTrue(line, f"прогон не дошёл до расписки:\n{proc.stdout[-2000:]}\n{proc.stderr[-3000:]}")
        out = json.loads(line[len("KEAT-E2E "):])
        self.assertTrue(out["ready"], out)
        self.assertEqual(out["history"], 4, out)
        self.assertEqual(out["sidecar"], {"history": 4, "current": 1}, out)
        self.assertEqual(out["first_tape"], 7,
                         f"в модель уехала не вся лента (4 записи истории + пары рук + сейчас): {out}")
        self.assertTrue(out["served"] and all(out["served"]), out)
        self.assertTrue(all(ms is not None and ms < 1000 for ms in out["canary_ms"]), out)
        self.assertGreaterEqual(out["epochs"], 1, out)


if __name__ == "__main__":
    unittest.main()
