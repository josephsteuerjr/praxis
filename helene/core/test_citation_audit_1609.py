# 16.09, аудит 15.09 (workspace/citation-delivery-audit-20260915.md): две ошибки
# _strip_citation_tokens — academia/news не чистились, а токен внутри URL ломал ссылку.
import unittest

import agent


class AcademiaNewsTokensStripped(unittest.TestCase):
    def test_academia_block_goes_away(self):
        self.assertEqual(
            agent._strip_citation_tokens("a turn0academia12 b"), "a  b")

    def test_news_token_goes_away(self):
        self.assertEqual(
            agent._strip_citation_tokens("a citeturn1news3 b"), "a  b")

    def test_academia_in_pua_wrapper(self):
        text = f"a \ue200cite\ue202turn0academia12\ue201 b"
        self.assertEqual(agent._strip_citation_tokens(text), "a  b")


class UrlWithGluedToken(unittest.TestCase):
    def test_trailing_slash_not_left_behind(self):
        # было: https://example.org/ (битый хвост-слэш)
        self.assertEqual(
            agent._strip_citation_tokens("см. https://example.org/turn0search1 там"),
            "см. https://example.org там")

    def test_token_mid_text_still_plain_stripped(self):
        self.assertEqual(
            agent._strip_citation_tokens("a turn1search3 b"), "a  b")

    def test_bare_trailing_slash_not_touched_when_no_token(self):
        self.assertEqual(
            agent._strip_citation_tokens("https://example.org/ ок"),
            "https://example.org/ ок")


if __name__ == "__main__":
    unittest.main()
