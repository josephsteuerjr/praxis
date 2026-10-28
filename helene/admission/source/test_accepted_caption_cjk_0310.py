"""03.10: accepted-калитка reconcile для send_file должна принимать
чищеный caption (сырой аргумент модели содержит CJK-артефакт, в леджер
попадает стрипнутый) — зеркало prepared-калитки, найдено адверсарным ревью."""
import hashlib
import pathlib
import unittest
import unittest.mock as mock

import agent
import telegram_outbox
from test_agent_resume_runtime import AgentResumeRuntimeTests


class AcceptedCaptionCjkTests(AgentResumeRuntimeTests):
    def test_accepted_caption_with_cjk_artifact_reconciles(self):
        context = self._create("accepted-caption-cjk")
        blob = pathlib.Path(self.base) / "caption-cjk-blob.bin"
        blob.write_bytes(b"durable report")
        digest = hashlib.sha256(blob.read_bytes()).hexdigest()
        raw_caption = "вот файл训练ный отчёт"
        cleaned = agent._strip_generation_artifacts(raw_caption)[0]
        self.assertNotEqual(raw_caption, cleaned)
        call_input = {"to": "42", "path": str(blob), "caption": raw_caption}
        key = f"telegram-outbox:{context.run_id}:tool:file-one"
        self.manager.start_tool(
            context.run_id, "file-one", "send_file", call_input,
            side_effect=True, idempotency_key=key,
        )
        self._pause(
            context,
            reason="durable send_file intent awaits Telegram acceptance",
        )
        random_id = telegram_outbox.stable_random_id(key)
        entry = {
            "state": "accepted", "key": key, "kind": "file",
            "run_id": context.run_id, "call_id": "file-one",
            "purpose": "tool:send_file", "peer_id": 42,
            "topic_id": None, "random_id": random_id,
            "payload": {
                "staged_path": str(blob),
                "size": blob.stat().st_size,
                "sha256": digest,
                "visible_filename": "caption-cjk-blob.bin",
                "caption": cleaned[:900],
            },
            "receipt": {"message_id": 601, "random_id": random_id},
        }
        # prepared-калитка тоже должна пропустить чищеный caption
        agent.run_direct_outbox_prepared(entry, target_label="peer 42")
        with mock.patch.dict(agent._TELETHON, {
            "project_direct_outbox_acceptance": lambda _proof, _entry: "ok",
        }):
            self.assertTrue(agent.run_direct_outbox_accepted(entry))


if __name__ == "__main__":
    unittest.main()
