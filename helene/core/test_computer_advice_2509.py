"""Совет руки `computer` про выделение в поле — по живому замеру E4 (25.09), пришпилен.

Откат на догадку («End/Escape или второй клик») снова учил бы модель затирать поле.

Запуск:  python praxis_test.py test_computer_advice_2509 -v
"""
import unittest

import agent


class ComputerAdviceIsTheMeasuredOne(unittest.TestCase):
    def test_description_names_click_or_end_and_warns_about_escape_and_double_click(self):
        desc = str(agent.COMPUTER_TOOL.get("description") or "")
        self.assertIn("click once into the field or press End before typing", desc)
        self.assertIn("Escape keeps the selection", desc)
        self.assertIn("double-click", desc)
        self.assertNotIn("press End/Escape or click a second time", desc, "прежняя догадка вернулась")


if __name__ == "__main__":
    unittest.main()
