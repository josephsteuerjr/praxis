"""An unbound call names what is wrong: an extra field to remove, a missing one to add.

Tim (Helene 2.7.2, local Gemma): after «unexpected keyword argument 'brief'» the hint said
«повтори с полным набором обязательных аргументов», which sent the model the wrong way.
"""
import unittest

import agent
import frame_language


def hand(path: str, limit: int = 10) -> str:
    return "never called"


def open_hand(path: str, **rest) -> str:
    return "never called"


class ArgsRefusal(unittest.TestCase):
    def call(self, impl, args):
        return agent._call_tool_with_ceiling_inner("fs_read", impl, args)

    def test_extra_field_says_remove_it_and_names_the_schema(self):
        text = self.call(hand, {"path": "a", "brief": True})
        self.assertIn("[аргументы не приняты]", text)
        self.assertIn("brief", text)
        self.assertIn("лишние", text)
        self.assertIn("path", text)  # the required list from the signature
        self.assertNotIn("полным набором", text)

    def test_missing_field_says_add_it(self):
        text = self.call(hand, {"limit": 3})
        self.assertIn("не хватает обязательных полей path", text)
        self.assertNotIn("лишние", text)

    def test_mixed_or_unclassified_errors_keep_the_raw_error(self):
        text = self.call(hand, {"brief": 1})
        self.assertIn("TypeError", text)
        self.assertIn("Сверься со схемой", text)

    def test_open_kwargs_never_call_a_field_extra(self):
        self.assertIn("не хватает обязательных полей path", self.call(open_hand, {"brief": 1}))

    def test_every_frame_language_has_the_three_texts(self):
        for language in ("ru", "en", "fr", "zh"):
            with self.subTest(language=language), frame_language.use(language):
                text = self.call(hand, {"path": "a", "brief": True})
                self.assertIn("brief", text)
                self.assertIn("`fs_read`", text)
                self.assertNotEqual(text, "")


if __name__ == "__main__":
    unittest.main()
