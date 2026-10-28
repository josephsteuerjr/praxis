"""Почта: send форматирует письмо и логинится; fetch парсит. Сеть замокана."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mailer


class TestConfigured(unittest.TestCase):
    def test_not_configured_without_env(self):
        with mock.patch.dict(os.environ, {"PRAXIS_EMAIL_ADDR": "", "PRAXIS_EMAIL_PASS": ""}):
            self.assertFalse(mailer.configured())
            self.assertIn("не настроена", mailer.send("a@b.com", "s", "b"))

    def test_configured_with_env(self):
        with mock.patch.dict(os.environ, {"PRAXIS_EMAIL_ADDR": "sender@example.invalid", "PRAXIS_EMAIL_PASS": "x"}):
            self.assertTrue(mailer.configured())


class TestSend(unittest.TestCase):
    def test_send_logs_in_and_sends(self):
        sent = {}

        class FakeSMTP:
            def __init__(self, host, port, context=None, timeout=None):
                sent["host"], sent["port"] = host, port

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def login(self, addr, pw):
                sent["login"] = (addr, pw)

            def send_message(self, msg):
                sent["msg"] = msg

        with mock.patch.dict(os.environ, {"PRAXIS_EMAIL_ADDR": "sender@example.invalid", "PRAXIS_EMAIL_PASS": "pw",
                                          "PRAXIS_SMTP_HOST": "smtp.example.invalid", "PRAXIS_SMTP_PORT": "465"}), \
                mock.patch.object(mailer, "SMTP_SSL", FakeSMTP):
            out = mailer.send("hope@x.com", "Привет", "тело")
        self.assertIn("Отправлено", out)
        self.assertEqual(sent["login"], ("sender@example.invalid", "pw"))
        self.assertEqual(sent["msg"]["To"], "hope@x.com")
        self.assertEqual(sent["msg"]["Subject"], "Привет")
        self.assertEqual(sent["host"], "smtp.example.invalid")

    def test_bad_address(self):
        with mock.patch.dict(os.environ, {"PRAXIS_EMAIL_ADDR": "p@m.com", "PRAXIS_EMAIL_PASS": "x"}):
            self.assertIn("Не похоже на адрес", mailer.send("notanemail", "s", "b"))


class TestFetch(unittest.TestCase):
    def test_fetch_parses(self):
        raw = (b"From: Hope <hope@x.com>\r\nSubject: Re: agents\r\n"
               b"Date: Mon, 30 Jun 2026 00:00:00 +0000\r\n"
               b"Content-Type: text/plain; charset=utf-8\r\n\r\nhello there\r\n")

        class FakeIMAP:
            def __init__(self, host, port, ssl_context=None):
                pass

            def login(self, a, p):
                pass

            def select(self, box):
                pass

            def search(self, charset, crit):
                return ("OK", [b"1"])

            def fetch(self, i, spec):
                return ("OK", [(b"1 (RFC822)", raw)])

            def logout(self):
                pass

        with mock.patch.dict(os.environ, {"PRAXIS_EMAIL_ADDR": "p@m.com", "PRAXIS_EMAIL_PASS": "x"}), \
                mock.patch.object(mailer, "IMAP4_SSL", FakeIMAP):
            msgs = mailer.fetch(limit=5)
        self.assertEqual(len(msgs), 1)
        self.assertIn("hello there", msgs[0]["body"])
        self.assertIn("Hope", msgs[0]["from"])
        self.assertEqual(msgs[0]["subject"], "Re: agents")

    def test_send_attachments(self):
        sent = {}

        class FakeSMTP:
            def __init__(self, host, port, context=None, timeout=None):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def login(self, addr, pw):
                pass

            def send_message(self, msg):
                sent["msg"] = msg

        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "data.log"
            f.write_text("line1\nline2\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"PRAXIS_EMAIL_ADDR": "s@e.invalid", "PRAXIS_EMAIL_PASS": "pw"}), \
                    mock.patch.object(mailer, "SMTP_SSL", FakeSMTP):
                status = mailer.send("x@y.zz", "тема", "тело", attachments=[str(f)])
        self.assertIn("Отправлено", status)
        self.assertIn("data.log (12 б)", status)
        msg = sent["msg"]
        self.assertTrue(msg.is_multipart())
        names = [p.get_filename() for p in msg.iter_attachments()]
        self.assertEqual(names, ["data.log"])

    def test_send_attachment_missing_file(self):
        sent = {}

        class FakeSMTP:
            def __init__(self, host, port, context=None, timeout=None):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def login(self, addr, pw):
                pass

            def send_message(self, msg):
                sent["msg"] = msg

        with mock.patch.dict(os.environ, {"PRAXIS_EMAIL_ADDR": "s@e.invalid", "PRAXIS_EMAIL_PASS": "pw"}), \
                mock.patch.object(mailer, "SMTP_SSL", FakeSMTP):
            status = mailer.send("x@y.zz", "тема", "тело",
                                 attachments=["/nonexistent/zzz.bin"])
        self.assertIn("Не отправилось", status)
        self.assertIn("не читается", status)
        self.assertNotIn("msg", sent)


if __name__ == "__main__":
    unittest.main()
