"""In-process actual worker-loop consumers; no Forge processes or model/network calls.

Run on the parent stand with the existing Forge/self-review suites as well. These
small tests deliberately fake only external Forge/model boundaries, not the loop,
checkpoint reader, journal, immutable payloads or SnapshotReviewer implementation.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


CORE = Path(__file__).resolve().parent
ROOT = CORE.parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Interrupted(BaseException):
    pass


class Adapter:
    tools = [{"name": "inspect", "input_schema": {"type": "object"}}]

    def prepare(self, llm, request):
        return {"framework": "fake", "allow_fallback": False}

    def system(self, request):
        return "exact frozen system"

    def dispatch(self, request, name, args):
        return "Refused" if name != "inspect" else "complete output"

    def diff(self):
        return "complete diff\n" * 1000

    def result_stamp(self):
        return {"adapter_verified": True}


class WorkerDurability(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="forge-durable-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.allowed = True
        self.responses = []
        self.calls = []
        fake_forge = types.ModuleType("forge")
        fake_forge._LIVE_UNIT_STATES = {"starting", "running", "finishing"}
        fake_forge._mutation_lock = lambda task: contextlib.nullcontext()
        fake_forge._read_json = lambda p: json.loads(Path(p).read_text("utf-8")) if Path(p).exists() else None
        fake_forge.worker_control = lambda path: {"allowed": self.allowed, "reason": "owner Pause"}
        fake_forge._admission_reason = lambda *args: "owner Pause" if not self.allowed else ""
        fake_forge._event = lambda *args, **kw: None
        fake_forge.emit_unit_event = lambda *args, **kw: None
        fake_forge.inspect = lambda *args, **kw: "real diff"
        fake_llm = types.ModuleType("llm")
        fake_llm.BrokenChannelError = type("BrokenChannelError", (Exception,), {})
        fake_llm.limits = lambda: types.SimpleNamespace(max_tool_iters=20)
        fake_llm.chat = self.chat
        fake_llm.snapshot = lambda: {"voice": {"framework": "fake", "model": "snapshot-model"}}
        fake_llm.assistant_message = lambda response, blocks: {
            "role": "assistant", "content": blocks,
            "provider_continuation": {"scope": "exact-route", "payload": "signed-carry"}}
        fake_context = types.ModuleType("run_context")
        fake_context.bind_run = lambda _: contextlib.nullcontext()
        self.patch = mock.patch.dict(sys.modules, {"forge": fake_forge, "llm": fake_llm,
                                                  "run_context": fake_context})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.runtime = load("forge_runtime", CORE / "forge_runtime.py")
        self.worker = load("_durability_actual_worker", CORE / "forge_worker.py")
        self.request_path, self.request = self.make_request("agent-first")

    def make_request(self, name, **extra):
        path = self.base / "agents" / name / "request.json"
        path.parent.mkdir(parents=True)
        request = {"id": name, "task_id": "code-test", "role": "worker", "root": str(self.base),
                   "brief": "do exact work", "max_iters": 50, **extra}
        self.runtime.atomic_json(path, request)
        return path, request

    def chat(self, *args, **kwargs):
        # Copy the call as the provider saw it; the actual messages object grows.
        self.calls.append(json.loads(json.dumps(kwargs)))
        if not self.responses:
            raise AssertionError("unexpected extra model call")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    @staticmethod
    def response(tool=None, index=0, text="done"):
        blocks = ([{"type": "tool_use", "id": "call-" + str(index), "name": tool, "input": {"value": index}}]
                  if tool else [{"type": "text", "text": text}])
        return types.SimpleNamespace(blocks=blocks, stop_reason="tool_use" if tool else "end_turn",
                                     text="" if tool else text, model="actual-fake-model", usage={})

    def run_worker(self, adapter=None, path=None, request=None, reviewer=None):
        return self.worker._run_request(path or self.request_path, request or self.request,
                                        adapter=adapter, reviewer=reviewer)

    def authorize(self, target=None, source=None):
        target = target or self.request_path.parent
        info = self.runtime.continuation_info(source or target)
        self.runtime.atomic_json(target / "resume.json", {"schema": 1, "authorized": True,
            "old_writer_ended": True, "task_control_permits": True,
            "generation": info["generation"], "checkpoint_sha256": info["checkpoint_sha256"]})
        return info

    def test_more_than_thirty_long_outputs_and_route_carry_survive(self):
        output = "кириллица🙂" * 2100
        adapter = Adapter()
        adapter.dispatch = lambda *args: output
        self.responses = [self.response("inspect", i) for i in range(35)] + [self.response()]
        self.assertEqual(self.run_worker(adapter), 0)
        result = json.loads((self.request_path.parent / "result.json").read_text("utf-8"))
        self.assertEqual(len(result["trace"]), 35)
        self.assertTrue(all(row["output"] == output for row in result["trace"]))
        checkpoint = json.loads((self.request_path.parent / "checkpoint.json").read_text("utf-8"))
        self.assertEqual(checkpoint["messages"][2]["content"][0]["content"], output)
        self.assertEqual(checkpoint["messages"][1]["provider_continuation"]["payload"], "signed-carry")
        self.assertEqual(checkpoint["max_iters"], 50)
        self.assertEqual(checkpoint["iteration"], 36)
        ref = result["trace"][0]["output_ref"]
        first = self.runtime.read_payload(self.request_path.parent, ref["sha256"], 0, 8000)
        second = self.runtime.read_payload(self.request_path.parent, ref["sha256"], first["next_offset"], 8000)
        self.assertEqual(first["text"] + second["text"], output[:16000])
        self.assertEqual(first["size_bytes"], len(output.encode("utf-8")))
        self.assertEqual(self.runtime.read_payload_full(self.request_path.parent, result["diff_ref"]), adapter.diff())
        page = self.runtime.read_events(self.request_path.parent, 0, 7)
        next_page = self.runtime.read_events(self.request_path.parent, page["next_after_seq"], 7)
        self.assertEqual(next_page["events"][0]["seq"], 8)
        self.assertTrue(page["has_more"])

    def test_intent_and_exact_args_are_persisted_before_dispatch(self):
        adapter = Adapter()
        def dispatch(request, name, args):
            cp = json.loads((self.request_path.parent / "checkpoint.json").read_text("utf-8"))
            self.assertEqual(cp["next_boundary"], "tool_in_flight")
            events = list(self.runtime._events(self.request_path.parent))
            self.assertEqual(events[-1]["kind"], "tool_start")
            self.assertEqual(json.loads(self.runtime.read_payload_full(
                self.request_path.parent, events[-1]["input_ref"])), args)
            return "effect completed"
        adapter.dispatch = dispatch
        self.responses = [self.response("inspect", 1), self.response()]
        self.assertEqual(self.run_worker(adapter), 0)

    def test_interrupted_unknown_tool_refuses_even_with_resume_receipt(self):
        adapter = Adapter()
        dispatch = mock.Mock(side_effect=Interrupted())
        adapter.dispatch = dispatch
        self.responses = [self.response("inspect", 1)]
        with self.assertRaises(Interrupted):
            self.run_worker(adapter)
        before = (self.request_path.parent / "checkpoint.json").read_bytes()
        info = self.authorize()
        self.assertFalse(info["safe"])
        self.assertEqual(self.run_worker(adapter), 2)
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual((self.request_path.parent / "checkpoint.json").read_bytes(), before)

    def _crash_after_result(self, *, before_checkpoint):
        adapter = Adapter()
        adapter.dispatch = mock.Mock(return_value="effect done once")
        self.responses = [self.response("inspect", 1)]
        save = self.runtime.Journal.save
        def crash(journal, boundary, phase, **fields):
            if boundary == "tools_ready" and journal.state.get("trace"):
                if not before_checkpoint:
                    save(journal, boundary, phase, **fields)
                raise Interrupted()
            return save(journal, boundary, phase, **fields)
        with mock.patch.object(self.runtime.Journal, "save", crash), self.assertRaises(Interrupted):
            self.run_worker(adapter)
        info = self.authorize()
        self.assertTrue(info["safe"])
        self.responses = [self.response(text="resumed from complete result")]
        self.assertEqual(self.run_worker(adapter), 0)
        self.assertEqual(adapter.dispatch.call_count, 1)
        self.assertEqual(self.calls[-1]["messages"][-1]["content"][0]["content"], "effect done once")
        return json.loads((self.request_path.parent / "result.json").read_text("utf-8"))

    def test_safe_checkpoint_continuation_does_not_repeat_tool(self):
        self.assertEqual(self._crash_after_result(before_checkpoint=False)["tool_calls"], 1)

    def test_result_event_before_checkpoint_is_reconciled_without_replay(self):
        self.assertEqual(self._crash_after_result(before_checkpoint=True)["tool_calls"], 1)

    def test_new_unit_continues_stopped_safe_transcript_and_preserves_old_result(self):
        adapter = Adapter()
        def dispatch(*args):
            self.allowed = False
            return "work before Pause"
        adapter.dispatch = dispatch
        self.responses = [self.response("inspect", 1)]
        self.assertEqual(self.run_worker(adapter), 2)
        old_result = (self.request_path.parent / "result.json").read_bytes()
        info = self.runtime.continuation_info(self.request_path.parent)
        self.assertTrue(info["safe"])
        path, request = self.make_request("agent-second", continuation_source={
            "unit_dir": str(self.request_path.parent), "generation": info["generation"],
            "checkpoint_sha256": info["checkpoint_sha256"]})
        self.authorize(path.parent, self.request_path.parent)
        self.allowed = True
        self.responses = [self.response(text="continued after explicit resume")]
        self.assertEqual(self.run_worker(adapter, path=path, request=request), 0)
        result = json.loads((path.parent / "result.json").read_text("utf-8"))
        self.assertEqual(result["trace"][0]["output"], "work before Pause")
        self.assertEqual((self.request_path.parent / "result.json").read_bytes(), old_result)

    def test_changed_checkpoint_or_missing_authority_refuses(self):
        adapter = Adapter()
        self.responses = [self.response("inspect", 1)]
        adapter.dispatch = mock.Mock(side_effect=Interrupted())
        with self.assertRaises(Interrupted):
            self.run_worker(adapter)
        self.assertEqual(self.run_worker(adapter), 2)
        self.authorize()
        cp = json.loads((self.request_path.parent / "checkpoint.json").read_text("utf-8"))
        cp["reply"] = "changed after authorization"
        self.runtime.atomic_json(self.request_path.parent / "checkpoint.json", cp)
        self.assertEqual(self.run_worker(adapter), 2)
        self.assertEqual(adapter.dispatch.call_count, 1)

    def test_pause_during_final_diff_cannot_publish_success(self):
        adapter = Adapter()
        def diff():
            self.allowed = False
            return "diff observed before final publication"
        adapter.diff = diff
        self.responses = [self.response()]
        self.assertEqual(self.run_worker(adapter), 2)
        result = json.loads((self.request_path.parent / "result.json").read_text("utf-8"))
        self.assertEqual(result["status"], "stopped")
        self.assertNotIn("complete", result)

    def test_partial_transport_response_retained_before_recorded_retry(self):
        partial = self.response("run", 99)
        error = self.worker.llm.BrokenChannelError("partial channel result")
        error.partial = partial
        adapter = Adapter()
        adapter.dispatch = mock.Mock(return_value="never dispatch partial")
        self.responses = [error, self.response()]
        with mock.patch.dict("os.environ", {"PRAXIS_FORGE_TRANSPORT_RETRY_PAUSE_SEC": "0"}):
            self.assertEqual(self.run_worker(adapter), 0)
        adapter.dispatch.assert_not_called()
        events = list(self.runtime._events(self.request_path.parent))
        receipt = next(row for row in events if row["kind"] == "model_transport_error")
        self.assertTrue(receipt["retry"])
        self.assertTrue(receipt["partial_not_dispatched"])
        retained = json.loads(self.runtime.read_payload_full(self.request_path.parent, receipt["output_ref"]))
        self.assertEqual(retained["blocks"], partial.blocks)
        self.assertEqual(retained["assistant"]["provider_continuation"]["payload"], "signed-carry")

    def test_unknown_model_boundary_never_replays_provider_call(self):
        self.responses = [Interrupted()]
        with self.assertRaises(Interrupted):
            self.run_worker(Adapter())
        info = self.authorize()
        self.assertFalse(info["safe"])
        self.assertEqual(info["next_boundary"], "model_in_flight")
        self.assertEqual(self.run_worker(Adapter()), 2)
        self.assertEqual(len(self.calls), 1)

    def test_changed_adapter_refuses_without_rewriting_checkpoint(self):
        self._crash_adapter_once()
        before = (self.request_path.parent / "checkpoint.json").read_bytes()
        self.authorize()
        changed = Adapter()
        changed.tools = [{"name": "extra-tool"}]
        self.assertEqual(self.run_worker(changed), 2)
        self.assertEqual((self.request_path.parent / "checkpoint.json").read_bytes(), before)
        self.assertTrue((self.request_path.parent / "resume.json").exists())

    def _crash_adapter_once(self):
        adapter = Adapter()
        self.responses = [self.response("inspect", 1)]
        save = self.runtime.Journal.save
        def crash(journal, boundary, phase, **fields):
            save(journal, boundary, phase, **fields)
            if boundary == "tools_ready" and journal.state.get("trace"):
                raise Interrupted()
        with mock.patch.object(self.runtime.Journal, "save", crash), self.assertRaises(Interrupted):
            self.run_worker(adapter)

    def test_terminal_redelivery_never_restarts_model(self):
        self.responses = [self.response()]
        self.assertEqual(self.run_worker(Adapter()), 0)
        old = (self.request_path.parent / "result.json").read_bytes()
        self.assertEqual(self.run_worker(Adapter()), 0)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual((self.request_path.parent / "result.json").read_bytes(), old)

    def test_exclusive_adapter_tools_even_for_worker_role(self):
        self.responses = [self.response("run", 1), self.response()]
        adapter = Adapter()
        self.assertEqual(self.run_worker(adapter), 0)
        self.assertEqual([row["name"] for row in self.calls[0]["tools"]], ["inspect"])
        self.assertEqual(len(adapter.tools), 1)
        result = json.loads((self.request_path.parent / "result.json").read_text("utf-8"))
        self.assertEqual(result["trace"][0]["output"], "Refused")
        with self.assertRaises(ValueError):
            self.run_worker(adapter, reviewer=adapter)

    def test_payload_corruption_and_torn_events_are_explicit_errors(self):
        self.responses = [self.response()]
        self.assertEqual(self.run_worker(Adapter()), 0)
        directory = self.request_path.parent
        result = json.loads((directory / "result.json").read_text("utf-8"))
        sha = result["diff_ref"]["sha256"]
        (directory / "payloads" / (sha + ".txt")).write_text("altered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.runtime.read_payload(directory, sha)
        with (directory / "events.jsonl").open("ab") as stream:
            stream.write(b'{"seq":')
        with self.assertRaisesRegex(ValueError, "incomplete"):
            list(self.runtime._events(directory))
        with self.assertRaises(ValueError):
            self.runtime.read_payload(directory, "../not-a-sha")

    def test_writer_lock_refuses_simultaneous_writer(self):
        first = self.runtime.Journal(self.request_path.parent)
        try:
            with self.assertRaises(self.runtime.ContinuationRefused):
                self.runtime.Journal(self.request_path.parent)
        finally:
            first.close()
        second = self.runtime.Journal(self.request_path.parent)
        second.close()

    def test_actual_snapshot_reviewer_keyword_is_compatible(self):
        review_module = load("self_review", ROOT / "desk" / "localharness" / "self_review.py")
        adapter_module = load("_actual_snapshot_reviewer", ROOT / "desk" / "localharness" / "self_review_worker.py")
        frozen = self.base / "frozen"
        frozen.mkdir()
        content = b"- old\n+ new\n"
        (frozen / "changes.diff").write_bytes(content)
        subject = {"manifest": [{"path": "changes.diff", "sha256": hashlib.sha256(content).hexdigest(),
                                  "size": len(content)}], "gaps": []}
        raw = json.dumps(subject).encode("utf-8")
        (frozen / "subject.json").write_bytes(raw)
        sha = hashlib.sha256(raw).hexdigest()
        review_module.validate_snapshot(frozen, sha)
        reviewer = adapter_module.SnapshotReviewer({"root": str(frozen), "subject_sha256": sha})
        self.request["role"] = "reviewer"
        self.runtime.atomic_json(self.request_path, self.request)
        self.responses = [self.response("inspect", 1), self.response(text='{"verdict":"clear"}')]
        self.assertEqual(self.run_worker(reviewer=reviewer), 0)
        result = json.loads((self.request_path.parent / "result.json").read_text("utf-8"))
        self.assertEqual(result["subject_sha256"], sha)
        self.assertTrue(result["snapshot_verified"])
        self.assertEqual(self.calls[0]["framework"], "fake")
        self.assertEqual([row["name"] for row in self.calls[0]["tools"]], ["inspect"])


if __name__ == "__main__":
    unittest.main()
