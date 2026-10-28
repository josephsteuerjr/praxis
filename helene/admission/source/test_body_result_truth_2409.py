"""Transport acceptance must not overwrite a negative body result."""
import unittest
from unittest.mock import patch

import body_client


class BodyResultTruth(unittest.TestCase):
    def test_call_preserves_body_truth_and_transport_separately(self):
        for transport, payload, expected in [
            (True, {"ok": False, "reason": "not_found"}, False),
            (True, {"ok": True}, True),
            (False, {"ok": True}, False),
            (False, {"ok": False}, False),
            (True, {"complete": False, "met": False}, True),
            (True, {"ok": "false"}, False),
            (False, {"transport_ok": True}, False),
        ]:
            with self.subTest(transport=transport, payload=payload), \
                 patch.object(body_client, "submit", return_value={"ok": True, "request_id": "r"}), \
                 patch.object(body_client, "response", return_value={
                     "ok": True, "response": {"type": "result", "ok": transport, "result": payload}}), \
                 patch.object(body_client, "_observed", side_effect=lambda c, a, e, r: r):
                result = body_client.call("desktop.element.find", {}, timeout=1)
                self.assertIs(result["ok"], expected)
                self.assertIs(result["transport_ok"], transport)
                self.assertEqual(result["result"], payload)
                receipt = body_client._with_server_frame(result, wait_s=1, truth_field="ok")
                self.assertIs(receipt["server_side"]["truth"], expected)
                for key in ("complete", "met"):
                    if key in payload:
                        self.assertIs(result[key], payload[key])


if __name__ == "__main__":
    unittest.main()
