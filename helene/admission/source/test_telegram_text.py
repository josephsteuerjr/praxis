"""Offline chunk integrity tests; no Telegram network or account required."""
import unittest

from telegram_text import split_text


class TelegramTextTests(unittest.TestCase):
    def check_chunks(self, text, limit):
        chunks = split_text(text, limit)
        self.assertEqual(''.join(chunks), text)
        self.assertTrue(all(0 < len(c.encode('utf-16-le')) // 2 <= limit for c in chunks))
        return chunks

    def test_link_label_whitespace_is_not_a_boundary(self):
        link = '[Название длинной статьи](https://example.org/path)'
        chunks = self.check_chunks('x' * 35 + ' ' + link + ' after' * 10, 64)
        self.assertTrue(any(link in chunk for chunk in chunks))

    def test_link_starting_chunk_is_completed_instead_of_cut_at_label(self):
        link = '[Название длинной статьи](https://example.org/path)'
        chunks = self.check_chunks(link + 'x' * 80, 64)
        self.assertTrue(chunks[0].startswith(link))

    def test_balanced_parentheses_url_stays_together(self):
        link = '[long label](https://example.org/a_(b))'
        chunks = self.check_chunks('x' * 30 + ' ' + link + 'tail' * 30, 50)
        self.assertTrue(any(link in chunk for chunk in chunks))

    def test_code_block_and_inline_code_stay_together(self):
        for code in ('```python\nx = 1\nprint(x)\n```', '`some long literal`'):
            with self.subTest(code=code):
                chunks = self.check_chunks('a' * 30 + ' ' + code + 'x' * 80, 40)
                self.assertTrue(any(code in chunk for chunk in chunks))

    def test_oversized_or_malformed_markup_is_still_lossless_and_bounded(self):
        for text in ('[label](https://x/' + 'z' * 300 + ')',
                     '```' + 'a b\n' * 100 + '```', '[not closed ' * 20,
                     '`not closed ' * 20, '😀' * 100):
            self.check_chunks(text, 40)

    def test_emoji_budget_in_markup(self):
        link = '[😀 label](https://x.org)'
        chunks = self.check_chunks('😀' * 15 + link + 'z' * 50, 40)
        self.assertTrue(any(link in chunk for chunk in chunks))

    def test_empty_and_invalid_budget(self):
        self.assertEqual(split_text(''), ())
        with self.assertRaises(ValueError):
            split_text('x', 0)
        with self.assertRaises(ValueError):
            split_text('😀', 1)

    def test_split_link_reaches_raw_send_as_one_text_url_entity(self):
        for label in ('Название длинной статьи', 'line one\nline two',
                      'line one\n\nline two', '😀 line one\nline two'):
            with self.subTest(label=label):
                self._check_link_raw_send(label)

    def _check_link_raw_send(self, label):
        import asyncio
        import types
        from unittest.mock import patch
        from telethon.extensions import markdown
        import mtproto_runner as runner

        class RawClient:
            def __init__(self):
                self.requests = []

            async def get_input_entity(self, entity):
                return entity

            async def _parse_message_text(self, message, parse_mode):
                return markdown.parse(message)

            async def __call__(self, request):
                self.requests.append(request)
                return object()

            def _get_response_message(self, request, response, entity):
                return types.SimpleNamespace(id=100 + len(self.requests))

        link = f'[{label}](https://example.org/path)'
        text = 'x' * 35 + ' ' + link + ' after' * 10
        chunks = self.check_chunks(text, 64)
        self.assertTrue(any(link in chunk for chunk in chunks))
        client = RawClient()

        async def send():
            with patch.object(runner, 'client', client):
                for i, chunk in enumerate(runner._split_telegram_text(text, 64)):
                    await runner._send_message_idempotent(
                        42, chunk, delivery_key=f'fixture:{i}', reply_to=7)

        asyncio.run(send())
        urls = [entity.url for request in client.requests
                for entity in request.entities if hasattr(entity, 'url')]
        self.assertEqual(urls, ['https://example.org/path'])
        rendered_labels = [
            request.message.encode('utf-16-le')[
                entity.offset * 2:(entity.offset + entity.length) * 2
            ].decode('utf-16-le')
            for request in client.requests for entity in request.entities
            if hasattr(entity, 'url')
        ]
        self.assertEqual(rendered_labels, [label])
        self.assertTrue(all(request.reply_to.reply_to_msg_id == 7
                            for request in client.requests))
        self.assertEqual(len({r.random_id for r in client.requests}), len(client.requests))

    def test_live_and_durable_splitters_share_boundaries(self):
        import agent
        import mtproto_runner
        text = 'a' * 30 + ' [long label](https://example.org) ' + 'tail ' * 20
        expected = split_text(text, 50)
        self.assertEqual(agent._split_durable_telegram_text(text, 50), expected)
        self.assertEqual(mtproto_runner._split_telegram_text(text, 50), expected)
