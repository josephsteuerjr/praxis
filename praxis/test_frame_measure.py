import copy
import json
import unittest

from frame_measure import Sample, compare


class MeasureTest(unittest.TestCase):
    def request(self):
        return {"system": [{"type": "text", "text": "PRIVATE_SYSTEM"}],
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "PRIVATE_MESSAGE"},
                    {"type": "image_url", "image_url": {"url": "PRIVATE_IMAGE"}}]}],
                "tools": [{"name": "PRIVATE_TOOL", "input_schema": {"type": "object"}}]}

    def test_same_call_complete_envelope_no_mutation_or_private_output(self):
        request = self.request()
        before = copy.deepcopy(request)
        call = object()
        out = compare(actual=Sample(call, request), candidate=Sample(call, request))
        self.assertEqual(out["pair_status"], "same_call")
        self.assertEqual(out["estimated_token_delta"], 0)
        expected = len(json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode())
        self.assertEqual(out["actual"]["json_utf8_bytes"], expected)
        self.assertNotIn("PRIVATE", json.dumps(out))
        self.assertEqual(out["provider_tokens"], {"actual": None, "candidate": None})
        self.assertEqual(request, before)

    def test_distinct_calls_are_not_compared(self):
        out = compare(actual=Sample(object(), self.request()),
                      candidate=Sample(object(), self.request()))
        self.assertEqual(out["pair_status"], "mismatch")
        self.assertIsNone(out["estimated_token_delta"])
        self.assertIsNone(out["actual"]["json_utf8_bytes"])

    def test_missing_and_unknown_are_not_zero(self):
        call = object()
        for request in ({}, {"system": "x", "messages": [], "tools": None},
                        {"system": "x", "messages": [object()], "tools": []}):
            out = compare(actual=Sample(call, request), candidate=Sample(call, self.request()))
            self.assertEqual(out["actual"]["status"], "unknown")
            self.assertIsNone(out["estimated_token_delta"])
        out = compare(actual=Sample(call, self.request()), candidate=None)
        self.assertEqual(out["actual"]["status"], "known")
        self.assertEqual(out["candidate"]["status"], "missing")
        self.assertIsNone(out["estimated_token_delta"])

    def test_tool_and_message_bytes_are_included(self):
        request = self.request()
        call = object()
        smaller = dict(request, messages=[], tools=[])
        out = compare(actual=Sample(call, request), candidate=Sample(call, smaller))
        self.assertLess(out["estimated_token_delta"], 0)

    def test_empty_is_known_and_unsupported_payload_stays_unknown(self):
        call = object()
        request = {"system": "", "messages": [], "tools": []}
        out = compare(actual=Sample(call, request), candidate=Sample(call, request))
        self.assertEqual(out["actual"]["status"], "known")
        request["messages"].append(float('nan'))
        out = compare(actual=Sample(call, request), candidate=Sample(call, request))
        self.assertEqual(out["actual"]["status"], "unknown")
        json.dumps(out, allow_nan=False)
