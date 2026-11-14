"""13.11.2026 — петля перечитывания картинок из файлов.

Ход 6c96f03e (личка, сверка формул из книги): 2 ч 12 мин, 522 вызова модели, 511 чтений
page-59.png / formula-008-source.png / next-detail.png по кругу, 72 млн входных токенов,
ни строки ответа. Два дефекта вместе:

1. картинки из файлов делили со скринами окно в ДВЕ живые картинки — три сличаемых
   файла вытесняли друг друга, а пометка говорила «скрин устарел»;
2. наблюдение о петле подписывало результат вместе с номером артефакта рана, и
   одинаковые чтения выглядели разными — наблюдение молчало.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import agent

JPG = b"\xff\xd8\xff\xe0" + b"DATA"


def _file_image(name: str, sha: str | None = None, seq: int = 1) -> dict:
    block = {"type": "image", "path": f"/app/memory/runs/r/artifacts/{seq:04d}-{name}.model.jpg",
             "mime": "image/jpeg", "detail": "auto", "origin": "file-observe"}
    if sha:
        block["sha256"] = sha
    return block


def _screen(name: str) -> dict:
    return {"type": "image", "path": f"C:/runs/a/artifacts/{name}", "mime": "image/png",
            "detail": "auto", "origin": "computer-observe"}


def _turn(*blocks: dict) -> dict:
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "x", "content": "ok"}, *blocks]}


def _live_images(messages: list[dict]) -> list[str]:
    return [b["path"] for m in messages if isinstance(m.get("content"), list)
            for b in m["content"] if isinstance(b, dict) and b.get("type") == "image"]


def _stubs(messages: list[dict]) -> list[str]:
    return [b["text"] for m in messages if isinstance(m.get("content"), list)
            for b in m["content"] if isinstance(b, dict) and b.get("type") == "text"]


class FileImageWindowTests(unittest.TestCase):
    def test_three_files_read_in_a_cycle_all_stay_visible(self):
        # Ровно форма 13.11: A, B, C, A, B, C … — прежде видно было только два, и
        # каждое новое чтение вытесняло то, с чем его надо было сличить.
        names = ["page-59", "formula-008-source", "next-detail"]
        messages = []
        for seq in range(1, 10):
            name = names[(seq - 1) % 3]
            messages.append(_turn(_file_image(name, sha=name * 4, seq=seq)))
            agent._prune_stale_screenshots(messages)
        live = _live_images(messages)
        self.assertEqual(len(live), 3)
        self.assertEqual({os.path.basename(p).split("-", 1)[1] for p in live},
                         {f"{n}.model.jpg" for n in names})
        # живые — самые свежие копии каждого файла
        self.assertEqual(live, [m["content"][1]["path"] for m in messages[-3:]])
        dupes = [s for s in _stubs(messages) if "тот же файл открыт" in s]
        self.assertEqual(len(dupes), 6)

    def test_duplicate_identity_falls_back_to_name_without_run_sequence(self):
        # Блоки, восстановленные без sha, опознаются по имени без номера артефакта.
        messages = [_turn(_file_image("page-58", seq=1)), _turn(_file_image("page-58", seq=7))]
        agent._prune_stale_screenshots(messages)
        self.assertEqual(_live_images(messages), [messages[1]["content"][1]["path"]])

    def test_window_beyond_budget_names_the_rule(self):
        n = agent._LIVE_FILE_IMAGES + 1
        messages = [_turn(_file_image(f"page-{i}", sha=f"{i:064d}", seq=i)) for i in range(n)]
        agent._prune_stale_screenshots(messages)
        self.assertEqual(len(_live_images(messages)), agent._LIVE_FILE_IMAGES)
        stub = messages[0]["content"][1]
        self.assertEqual(stub["type"], "text")
        self.assertIn("page-0", stub["text"])
        self.assertIn(f"последние {agent._LIVE_FILE_IMAGES}", stub["text"])
        self.assertIn("склей", stub["text"])
        self.assertNotIn("устарел", stub["text"])

    def test_screens_and_files_keep_separate_windows_and_photos_are_untouched(self):
        photo = {"type": "image", "path": "/app/memory/inbox/from_egor.jpg", "mime": "image/jpeg"}
        messages = [
            {"role": "user", "content": [photo, {"type": "text", "text": "смотри"}]},
            _turn(_file_image("page-1", sha="a" * 64, seq=1)),
            _turn(_screen("shot1.png")),
            _turn(_screen("shot2.png")),
            _turn(_file_image("page-2", sha="b" * 64, seq=4)),
            _turn(_screen("shot3.png")),
        ]
        agent._prune_stale_screenshots(messages)
        self.assertEqual(messages[0]["content"][0], photo)
        self.assertEqual(messages[1]["content"][1]["type"], "image")      # файл жив
        self.assertIn("устарел", messages[2]["content"][1]["text"])        # старый скрин скрыт
        self.assertEqual(messages[3]["content"][1]["type"], "image")
        self.assertEqual(messages[4]["content"][1]["type"], "image")
        self.assertEqual(messages[5]["content"][1]["type"], "image")

    def test_pruning_is_stable_on_repeat(self):
        messages = [_turn(_file_image(f"p{i}", sha=f"{i:064d}", seq=i)) for i in range(9)]
        agent._prune_stale_screenshots(messages)
        once = [dict(b) for m in messages for b in m["content"]]
        agent._prune_stale_screenshots(messages)
        self.assertEqual(once, [dict(b) for m in messages for b in m["content"]])


class FsReadOriginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "PRAXIS_BASE": self.tmp.name, "PRAXIS_OWNER_ID": "101", "PRAXIS_TEST": "1",
        })
        self.env.start()
        self.base = Path(self.tmp.name)
        (self.base / "workspace").mkdir()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_fs_read_image_is_a_file_observation_with_source_sha(self):
        import workshop

        image = self.base / "workspace" / "page-59.jpg"
        image.write_bytes(JPG)
        with mock.patch.object(workshop, "BASE", self.base), \
                mock.patch.object(agent, "MEM_DIR", self.base / "memory"), \
                mock.patch.object(agent.llm, "can_see", return_value=True):
            seen = agent.tool_fs_read(str(image))
        self.assertIsInstance(seen, agent.ToolObservation)
        self.assertEqual(seen.images[0]["origin"], "file-observe")
        self.assertEqual(seen.images[0]["sha256"], hashlib.sha256(JPG).hexdigest())


class LoopObservationIdentityTests(unittest.TestCase):
    def test_run_local_sequence_numbers_do_not_count_as_a_different_result(self):
        one = ('Visual observation captured from a file in my home.\n'
               '{"artifact_id": "artifact-0003", "path": "artifacts/0003-9a1cf01847e5-page-59.png",'
               ' "sha256": "9a1c"}')
        two = one.replace("0003", "0509")
        self.assertEqual(agent._loop_identity_text(one), agent._loop_identity_text(two))
        changed = two.replace('"sha256": "9a1c"', '"sha256": "ffff"')
        self.assertNotEqual(agent._loop_identity_text(one), agent._loop_identity_text(changed))

    def test_repeated_image_reads_raise_the_loop_observation(self):
        observed = []
        responses = [
            types.SimpleNamespace(stop_reason="tool_use", text="", blocks=[{
                "type": "tool_use", "id": f"read-{i}", "name": "eyes",
                "input": {"path": "workspace/glezer/page-59.png"},
            }]) for i in range(3)
        ] + [types.SimpleNamespace(stop_reason="end_turn", text="done", blocks=[])]

        def fake_chat(*_args, **kwargs):
            observed.append([list(m["content"]) if isinstance(m.get("content"), list)
                             else m.get("content") for m in kwargs["messages"]])
            return responses.pop(0)

        counter = {"n": 0}

        def eyes(path=""):
            counter["n"] += 1
            n = counter["n"]
            return agent.ToolObservation(
                "Visual observation captured from a file in my home.\n"
                f'{{"artifact_id": "artifact-{n:04d}", '
                f'"path": "artifacts/{n:04d}-9a1cf01847e5-page-59.png", "sha256": "9a1c"}}',
                images=({"type": "image", "path": f"/tmp/obs{n}.jpg", "mime": "image/jpeg",
                         "detail": "auto", "origin": "file-observe", "sha256": "9a1c"},),
            )

        with mock.patch.object(agent.llm, "chat", side_effect=fake_chat), \
                mock.patch.dict(agent.TOOL_IMPL, {"eyes": eyes}):
            out = agent._terminal_tool_loop(
                system="test", messages=[], tools=[{"name": "eyes"}],
            )
        self.assertEqual(out, "done")
        final = observed[-1]
        notes = [b["text"] for row in final if isinstance(row, list)
                 for b in row if isinstance(b, dict) and b.get("type") == "text"
                 and "Loop observation" in str(b.get("text"))]
        self.assertEqual(len(notes), 1)
        self.assertIn("repeated 3 times", notes[0])
        images = [b["path"] for row in final if isinstance(row, list)
                  for b in row if isinstance(b, dict) and b.get("type") == "image"]
        self.assertEqual(images, ["/tmp/obs3.jpg"])  # тот же файл держит одно место


if __name__ == "__main__":
    unittest.main()
