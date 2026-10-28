"""03.10: прямые руки (send_message, narrate, адресный caption) чистят артефакты
генерации — CJK-вклейки, <think>. Утечка «训练ный» в Ouroboros 18:38 ушла сырой:
гард снимает артефакты только на голосе."""
import unittest
import unittest.mock as mock
import agent


class TestSendMessageCleansed(unittest.TestCase):
    def _send(self, text):
        m = mock.Mock(return_value="отправлено")
        with mock.patch.dict(agent._TELETHON, {"send_message": m}):
            agent.tool_send_message(123, text)
        return m.call_args[0][1]

    def test_intraword_cjk_is_sanitized(self):
        sent = self._send("в моём рантайме训练ный так")
        self.assertNotIn("训练ный", sent)
        self.assertIn("⟨вклейка", sent)

    def test_think_is_stripped(self):
        sent = self._send("<think>черновик</think>чистый текст")
        self.assertEqual(sent, "чистый текст")

    def test_clean_text_unchanged(self):
        self.assertEqual(self._send("обычная реплика"), "обычная реплика")

    def test_empty_after_cleanse_not_sent(self):
        m = mock.Mock(return_value="не должен вызываться")
        with mock.patch.dict(agent._TELETHON, {"send_message": m}):
            agent.tool_send_message(123, "<think>только черновик</think>")
        m.assert_not_called()


class TestNarrateCleansed(unittest.TestCase):
    def test_narrate_strips_cjk(self):
        from core import narration as core_narration
        m = mock.Mock(return_value="ушло")
        with mock.patch.object(core_narration, "enabled", return_value=True), \
             mock.patch.object(core_narration, "credential_floor", return_value=""), \
             mock.patch.object(agent, "private_record_floor", return_value=""), \
             mock.patch.object(core_narration, "is_duplicate", return_value=False), \
             mock.patch.object(core_narration, "gap_remaining", return_value=0.0), \
             mock.patch.object(agent, "_active_scope", return_value="owner"), \
             mock.patch.dict(agent._TELETHON, {"send_message": m}):
            agent.tool_narrate("процесс训练ный идёт")
        sent = m.call_args[0][1]
        self.assertNotIn("训练ный", sent)
        self.assertIn("⟨вклейка", sent)


class TestSendMediaCaptionCleansed(unittest.TestCase):
    def test_addressed_caption_strips_cjk(self):
        import pathlib
        import workshop as ws
        m = mock.Mock(return_value="отправлено")
        png = pathlib.Path("/tmp/x.png")
        with mock.patch.object(ws, "_resolve_read", return_value=png), \
             mock.patch.object(pathlib.Path, "is_file", return_value=True), \
             mock.patch.dict(agent._TELETHON, {"send_file": m}):
            agent.tool_send_media("/tmp/x.png", "photo", caption="подпись训练ный", to="bob")
        sent = m.call_args[0][1]
        self.assertNotIn("训练ный", sent)


if __name__ == "__main__":
    unittest.main()
