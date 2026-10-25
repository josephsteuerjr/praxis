"""Edition image requests through real llm.chat and SDK adapters, without network."""
from __future__ import annotations

import base64
import copy
import io
import json
import os
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
os.environ["PRAXIS_TEST"] = "1"
sys.path[:0] = [str(ROOT / "helene/core"), str(ROOT / "praxis")]
import _sandbox
assert _sandbox.activate_if_testing()
import llm


class CaptureSDK:
    def __init__(self, framework):
        self.framework = framework
        self.calls = []
        self.error = None
        self.base_url = "http://127.0.0.1:5011/v1"
        self.chat = SimpleNamespace(completions=self)
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if self.error is not None:
            raise self.error
        if self.framework == "anthropic":
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="fixture answer")],
                stop_reason="end_turn", model=kwargs["model"],
                usage=SimpleNamespace(input_tokens=4, output_tokens=2))
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
                content="fixture answer", tool_calls=[]))],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2))


def png_bytes(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=0)
    return buffer.getvalue()


def wire_images(call):
    images = []
    for message in call["messages"]:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "image_url":
                header, data = block["image_url"]["url"].split(",", 1)
                images.append((header.split(";")[0][5:], base64.b64decode(data)))
            elif block.get("type") == "image":
                source = block["source"]
                images.append((source["media_type"], base64.b64decode(source["data"])))
    return images


class ImageDelivery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.small = png_bytes(Image.new("RGB", (20, 16), (230, 10, 40)))
        cls.large = png_bytes(Image.frombytes(
            "RGB", (1450, 1450), random.Random(192).randbytes(1450 * 1450 * 3)))
        assert llm.IMAGE_REQUEST_MAX_BYTES < len(cls.large) < llm._VISION_MAX_BYTES

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.openai = CaptureSDK("openai")
        self.anthropic = CaptureSDK("anthropic")
        self.cfg = llm._normalize({"roles": {"voice": {
            "framework": "openai", "model": "gpt-6.1-sol", "vision_prepass": True,
            "fallback_model": ""}}})
        self.patch(patch.object(llm, "_config", return_value=self.cfg))
        self.patch(patch.object(llm, "_client_for", side_effect=lambda framework: {
            "openai": self.openai, "anthropic": self.anthropic}[framework]))
        self.patch(patch.object(llm, "_available_models", side_effect=lambda framework: {
            "openai": ["gpt-6.1-sol"], "anthropic": ["claude-sonnet-4"]}[framework]))
        self.patch(patch.object(llm, "_endpoint_hold", return_value=None))
        self.patch(patch.object(llm, "_brain_note"))
        self.patch(patch.object(llm, "_usage_add"))
        self.patch(patch.object(llm, "_journal"))
        self.patch(patch.object(llm, "_CALL_TRACE", self.root / "calls.jsonl"))
        self.patch(patch.dict(os.environ, {llm.VISION_PREPASS_LEVER: ""}))
        original_state = copy.deepcopy(llm._STATE)
        self.addCleanup(lambda: llm._STATE.update(original_state))
        self.patch(patch.object(llm, "NET_RETRIES", 0))

    def patch(self, context):
        value = context.start()
        self.addCleanup(context.stop)
        return value

    def block(self, data=None, name="image.png"):
        path = self.root / name
        if data is not None:
            path.write_bytes(data)
        return {"type": "image", "path": str(path), "mime": "image/png"}

    def call(self, blocks, framework="openai"):
        self.cfg["roles"]["voice"].update(
            framework=framework, model=("gpt-6.1-sol" if framework == "openai"
                                        else "claude-sonnet-4"))
        return llm.chat("voice", messages=[{"role": "user", "content": [
            {"type": "text", "text": "inspect these images in order"}, *blocks]}])

    def stats(self):
        rows = [json.loads(line) for line in (self.root / "calls.jsonl").read_text(
            encoding="utf-8").splitlines()]
        return rows[-1]["images"]

    def test_actual_documented_preset_models_keep_pixels(self):
        self.patch(patch.object(llm, '_available_models', side_effect=lambda framework: [self.cfg['roles']['voice']['model']]))
        for model in ('MiniMax-M3.1-Flash', 'MiniMax-M3', 'kimi-k3', 'deepseek-flash', 'deepseek-v4-pro', 'qwen3.8-max'):
            with self.subTest(model=model):
                self.cfg['roles']['voice'].update(framework='anthropic',model=model)
                response=llm.chat('voice',messages=[{'role':'user','content':[self.block(self.small)]}])
                self.assertEqual(wire_images(self.anthropic.calls[-1]), [('image/png',self.small)])
                self.assertEqual(self.anthropic.calls[-1]['model'],model)
                self.assertFalse(response.text.startswith(llm._NO_PIXELS_RESPONSE))
        self.assertFalse(llm.accepts_images(model='unqualified-local-model'))
        self.assertFalse(llm.accepts_images(model='glm-5.3'))

    def test_direct_openai_default_responses_images_and_tool_replay(self):
        import importlib
        import openai
        http=importlib.import_module(openai.DefaultHttpxClient.__mro__[1].__module__.split('.')[0])
        bodies=[]
        raw=[{'type':'reasoning','id':'rs_fixture','summary':[], 'encrypted_content':'opaque'},
             {'type':'function_call','id':'fc_fixture','call_id':'call_fixture','name':'probe','arguments':'{}','status':'completed'}]
        def respond(request):
            bodies.append(json.loads(request.content))
            output=raw if len(bodies)==1 else [{'type':'message','id':'msg_fixture','role':'assistant','status':'completed',
                'content':[{'type':'output_text','text':'fixture complete','annotations':[]}]}]
            result={'id':'resp_fixture','object':'response','created_at':1,'status':'completed','model':'gpt-5.4','output':output,
                    'usage':{'input_tokens':12,'output_tokens':3,'total_tokens':15,
                             'input_tokens_details':{'cached_tokens':0},'output_tokens_details':{'reasoning_tokens':1}}}
            text='event: response.completed\ndata: '+json.dumps({'type':'response.completed','response':result})+'\n\n'
            return http.Response(200,text=text,headers={'content-type':'text/event-stream'})
        self.cfg['roles']['voice'].update(framework='openai',model='gpt-5.4')
        self.patch(patch.object(llm, '_available_models', return_value=['gpt-5.4']))
        messages=[{'role':'user','content':[{'type':'text','text':'look'},self.block(self.small)]}]
        with openai.OpenAI(api_key='fixture',base_url='https://api.openai.com/v1',
                http_client=http.Client(transport=http.MockTransport(respond),trust_env=False)) as client, \
                patch.object(llm,'_client_for',return_value=client):
            first=llm.chat('voice',messages=messages,tools=[{'name':'probe','input_schema':{'type':'object','properties':{}}}])
            messages += [llm.assistant_message(first), {'role':'user','content':[{'type':'tool_result','tool_use_id':'call_fixture','content':'ok'}]}]
            llm.chat('voice',messages=messages)
        images=[block for message in bodies[0]['input'] for block in (message.get('content') or []) if isinstance(block,dict) and block.get('type')=='input_image']
        self.assertEqual(len(images),1)
        self.assertEqual(bodies[0]['model'],'gpt-5.4')
        self.assertEqual(base64.b64decode(images[0]['image_url'].split(',',1)[1]),self.small)
        self.assertIn(raw[0],bodies[1]['input'])
        self.assertTrue(any(item.get('type')=='function_call_output' for item in bodies[1]['input']))

    def test_small_image_bytes_reach_real_adapters_unchanged(self):
        self.assertEqual(Path(llm.__file__).resolve(), ROOT / "helene/core/llm.py")
        for framework, sdk in (("openai", self.openai), ("anthropic", self.anthropic)):
            with self.subTest(framework=framework):
                block = self.block(self.small)
                original = copy.deepcopy(block)
                response = self.call([block], framework)
                self.assertEqual(wire_images(sdk.calls[-1]), [("image/png", self.small)])
                self.assertEqual(block, original)
                self.assertEqual(Path(block["path"]).read_bytes(), self.small)
                self.assertFalse(response.text.startswith(llm._NO_PIXELS_RESPONSE))
                self.assertEqual(self.stats()["prepared"], 1)
                self.assertEqual(self.stats()["prepared_bytes"], len(self.small))
                self.assertFalse(self.stats()["all_pixels_omitted"])

    def test_large_image_is_a_bounded_derivative_and_original_is_retained(self):
        for framework, sdk in (("openai", self.openai), ("anthropic", self.anthropic)):
            with self.subTest(framework=framework):
                block = self.block(self.large)
                response = self.call([block], framework)
                images = wire_images(sdk.calls[-1])
                self.assertEqual(len(images), 1)
                mime, payload = images[0]
                self.assertEqual(mime, "image/jpeg")
                self.assertLessEqual(len(payload), llm.IMAGE_REQUEST_MAX_BYTES)
                with Image.open(io.BytesIO(payload)) as image:
                    self.assertEqual(image.size, (1450, 1450))
                    image.load()
                self.assertEqual(Path(block["path"]).read_bytes(), self.large)
                self.assertIn("derivative", json.dumps(sdk.calls[-1]["messages"]))
                self.assertFalse(response.text.startswith(llm._NO_PIXELS_RESPONSE))
                stats = self.stats()
                self.assertEqual((stats["input"], stats["prepared"], stats["resized"]), (1, 1, 1))
                self.assertEqual(stats["source_bytes_known"], len(self.large))
                self.assertEqual(stats["prepared_bytes"], len(payload))

    def test_missing_image_is_unavailable_not_count_or_size_and_marks_response(self):
        response = self.call([self.block()])
        self.assertEqual(wire_images(self.openai.calls[-1]), [])
        text = json.dumps(self.openai.calls[-1]["messages"])
        self.assertIn("unavailable", text)
        self.assertNotIn("request budget keeps", text)
        self.assertTrue(response.text.startswith(llm._NO_PIXELS_RESPONSE))
        self.assertEqual(response.blocks[0]["text"], llm._NO_PIXELS_RESPONSE)
        self.assertEqual(self.stats()["omitted_unavailable"], 1)
        self.assertTrue(self.stats()["all_pixels_omitted"])

    def test_count_budget_keeps_newest_available_images_and_reports_exact_counts(self):
        self.patch(patch.dict(llm.IMAGE_REQUEST_BUDGET, {"openai": 2}))
        first = self.block(png_bytes(Image.new("RGB", (4, 4), "red")), "first.png")
        second = self.block(png_bytes(Image.new("RGB", (4, 4), "green")), "second.png")
        third = self.block(png_bytes(Image.new("RGB", (4, 4), "blue")), "third.png")
        response = self.call([first, second, self.block(name="missing.png"), third])
        self.assertEqual([data for _, data in wire_images(self.openai.calls[-1])], [
            Path(second["path"]).read_bytes(), Path(third["path"]).read_bytes()])
        self.assertIn("last 2 available images", json.dumps(self.openai.calls[-1]["messages"]))
        stats = self.stats()
        self.assertEqual((stats["input"], stats["prepared"], stats["omitted_count"],
                          stats["omitted_unavailable"]), (4, 2, 1, 1))
        self.assertFalse(stats["all_pixels_omitted"])
        self.assertFalse(response.text.startswith(llm._NO_PIXELS_RESPONSE))

    def test_unconvertible_oversize_is_size_omission_not_count_budget(self):
        block = self.block(b"not an image" + bytes(llm.IMAGE_REQUEST_MAX_BYTES))
        response = self.call([block])
        self.assertEqual(wire_images(self.openai.calls[-1]), [])
        text = json.dumps(self.openai.calls[-1]["messages"])
        self.assertIn("bounded derivative could not be prepared", text)
        self.assertNotIn("request budget keeps", text)
        self.assertEqual(self.stats()["omitted_size"], 1)
        self.assertTrue(response.text.startswith(llm._NO_PIXELS_RESPONSE))

    def test_base64_image_over_limit_also_gets_a_derivative(self):
        block = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                "data": base64.b64encode(self.large).decode("ascii")}}
        self.call([block])
        self.assertEqual(wire_images(self.openai.calls[-1])[0][0], "image/jpeg")
        self.assertEqual(self.stats()["resized"], 1)

    def test_decoder_safety_failure_becomes_an_honest_size_omission(self):
        self.patch(patch.object(Image, "open", side_effect=Image.DecompressionBombError("fixture")))
        response = self.call([self.block(self.large)])
        self.assertEqual(wire_images(self.openai.calls[-1]), [])
        self.assertEqual(self.stats()["omitted_size"], 1)
        self.assertTrue(response.text.startswith(llm._NO_PIXELS_RESPONSE))

    def test_missing_decoder_does_not_claim_pixels_or_break_small_images(self):
        self.patch(patch.dict(sys.modules, {"PIL": None}))
        response = self.call([self.block(self.large)])
        self.assertTrue(response.text.startswith(llm._NO_PIXELS_RESPONSE))
        self.assertEqual(self.stats()["omitted_size"], 1)
        self.call([self.block(self.small)])
        self.assertEqual(wire_images(self.openai.calls[-1]), [("image/png", self.small)])

    def test_derivative_bounds_dimensions_and_preserves_alpha_against_white(self):
        self.patch(patch.object(llm, "IMAGE_REQUEST_MAX_BYTES", 5000))
        self.patch(patch.object(llm, "IMAGE_REQUEST_MAX_EDGE", 128))
        original = png_bytes(Image.new("RGBA", (512, 256), (0, 0, 0, 0)))
        self.call([self.block(original)])
        payload = wire_images(self.openai.calls[-1])[0][1]
        with Image.open(io.BytesIO(payload)) as image:
            self.assertEqual(image.size, (128, 64))
            self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))

    def test_animated_oversize_is_not_silently_relabelled_a_static_image(self):
        self.patch(patch.object(llm, "IMAGE_REQUEST_MAX_BYTES", 40))
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(
            buffer, format="GIF", save_all=True,
            append_images=[Image.new("RGB", (8, 8), "blue")])
        block = self.block(buffer.getvalue(), "animation.gif")
        block["mime"] = "image/gif"
        response = self.call([block])
        self.assertEqual(wire_images(self.openai.calls[-1]), [])
        self.assertTrue(response.text.startswith(llm._NO_PIXELS_RESPONSE))

    def test_tool_continuation_keeps_pixels_and_does_not_mutate_original_tape(self):
        block = self.block(self.large)
        tape = [{"role": "user", "content": [block]}]
        original = copy.deepcopy(tape)
        llm.chat("voice", messages=tape)
        first_payload = wire_images(self.openai.calls[-1])
        self.assertEqual(tape, original)
        tape.extend([
            {"role": "assistant", "content": [{"type": "tool_use", "id": "fixture-1",
              "name": "inspect", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "fixture-1",
                                            "content": "result"}]}])
        checkpoint = json.loads(json.dumps(tape))
        llm.chat("voice", messages=checkpoint)
        self.assertEqual(wire_images(self.openai.calls[-1]), first_payload)
        self.assertEqual(checkpoint, tape)
        self.assertEqual(self.openai.calls[-1]["messages"][-1]["role"], "tool")
        self.assertEqual(self.openai.calls[-1]["messages"][-1]["tool_call_id"], "fixture-1")

    def test_fallback_uses_its_own_count_budget_and_reports_its_actual_payload(self):
        self.cfg["roles"]["voice"].update(fallback_model="claude-sonnet-4",
                                           fallback_framework="anthropic")
        self.openai.error = TimeoutError("fixture channel timeout")
        self.patch(patch.dict(llm.IMAGE_REQUEST_BUDGET, {"openai": 1, "anthropic": 2}))
        first = self.block(self.small, "first.png")
        second = self.block(self.large, "second.png")
        response = self.call([first, second])
        self.assertEqual(len(wire_images(self.openai.calls[-1])), 1)
        self.assertEqual(len(wire_images(self.anthropic.calls[-1])), 2)
        self.assertEqual(self.stats()["prepared"], 2)
        self.assertEqual(self.stats()["omitted_count"], 0)
        self.assertEqual(response.framework, "anthropic")


if __name__ == "__main__":
    unittest.main()
