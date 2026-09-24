"""Single-process contract test for the bootstrap client's result projection."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "localharness"))
import body


class RawResultTruth(unittest.TestCase):
    def test_raw_call_cannot_turn_body_refusal_into_success(self):
        for transport, payload, expected in [
            (True, {"ok": False, "reason": "not_found"}, False),
            (True, {"ok": True}, True),
            (False, {"ok": True}, False),
            (False, {"ok": False}, False),
            (True, {"complete": False}, True),
            (True, {"ok": "false"}, False),
            (False, {"transport_ok": True}, False),
        ]:
            with self.subTest(transport=transport, payload=payload), \
                 patch.object(body, "_settings", return_value=("http://localhost", "test", "device")), \
                 patch.object(body, "_raw_request", side_effect=[
                     {"ok": True},
                     {"ok": True, "response": {"type": "result", "ok": transport, "result": payload}},
                 ]):
                result = body._raw_call("desktop.element.find", {}, timeout=1)
                self.assertIs(result["ok"], expected)
                self.assertIs(result["transport_ok"], transport)
                self.assertEqual(result["result"], payload)

    def test_console_fact_and_explanation_survive_status_projection(self):
        probe = object.__new__(body.Body)
        with patch.dict(body.STATE, clear=True), patch.object(body, "call", return_value={
            "ok": True, "platform": "macos", "console": False, "tcc": None,
            "hints": ["screen belongs to another session"],
        }):
            probe.probe_desktop()
            self.assertIs(body.STATE["console"], False)
            self.assertIsNone(body.STATE["tcc"])
            self.assertEqual(body.STATE["hints"], ["screen belongs to another session"])


if __name__ == "__main__":
    unittest.main()
