"""
PASS 8.0 — llm.py: конфиг (миграция/подхват), трансляция anthropic<->openai, фолбэк, снапшот.
Герметичны: сеть не зовётся (фейки в llm._TEST_CLIENTS), конфиг — в tmp.

Запуск:  python praxis_test.py test_llm -v
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest import mock
from pathlib import Path

import llm
import praxis_time


class FakeAnthResp:
    def __init__(self, text="ок", stop="end_turn", model=None):
        self.stop_reason = stop
        self.content = [types.SimpleNamespace(type="text", text=text)]
        self.usage = types.SimpleNamespace(input_tokens=10, output_tokens=3)
        self.model = model


class FakeAnthropic:
    def __init__(self, replies=None, error=None):
        self._r = list(replies or [])
        self.error = error
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.error is not None:
            raise self.error
        return self._r.pop(0) if self._r else FakeAnthResp()


class RateLimitError(Exception):
    pass


def _openai_resp(text="ок", tool_calls=None, finish="stop"):
    msg = types.SimpleNamespace(content=text, tool_calls=tool_calls or [])
    choice = types.SimpleNamespace(message=msg, finish_reason=finish)
    usage = types.SimpleNamespace(prompt_tokens=7, completion_tokens=2)
    return types.SimpleNamespace(choices=[choice], usage=usage)


class FakeOpenAI:
    def __init__(self, resp=None):
        self.calls = []
        self.resp = resp or _openai_resp()
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kw):
        self.calls.append(kw)
        return self.resp


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="praxis_llm_"))
        self._orig = [(llm, k, getattr(llm, k)) for k in ("CONFIG_PATH", "JOURNAL_DIR")]
        llm.CONFIG_PATH = self.tmp / "llm.json"
        llm.JOURNAL_DIR = self.tmp / "journal"
        self._cache0 = dict(llm._CACHE)
        llm._CACHE.update(mtime=None, cfg=None)
        self._state0 = {r: dict(s) for r, s in llm._STATE.items()}
        for s in llm._STATE.values():
            s.update(on_fallback=False, last_error="")
        llm.clear_test_clients()
        self._env = {k: os.environ.get(k) for k in
                     ("GLM_API_KEY", "GLM_BASE_URL", "GLM_VOICE_MODEL", "GLM_MODEL",
                      "GLM_FIRSTPASS_MODEL", "GLM_GATEKEEPER_MODEL", "OPENAI_API_KEY",
                      "OPENAI_BASE_URL", "PRAXIS_MAX_TOOL_ITERS")}
        for k in self._env:  # детерминизм: живой env (контейнер грузит .env) не влияет
            os.environ.pop(k, None)

    def tearDown(self):
        for mod, k, v in self._orig:
            setattr(mod, k, v)
        llm._CACHE.clear()
        llm._CACHE.update(self._cache0)
        for r, s in self._state0.items():
            llm._STATE[r].update(s)
        llm.clear_test_clients()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_cfg(self, **roles_over):
        cfg = llm._from_env()
        cfg["frameworks"]["anthropic"]["api_key"] = "zai-key"
        for role, over in roles_over.items():
            cfg["roles"][role].update(over)
        llm.save_config(cfg)
        return llm._config()


class TestConfig(Base):
    def test_migration_from_env(self):
        os.environ.update(GLM_API_KEY="k1", GLM_VOICE_MODEL="glm-9",
                          GLM_FIRSTPASS_MODEL="glm-mini", PRAXIS_MAX_TOOL_ITERS="7")
        cfg = llm._config()
        self.assertTrue(llm.CONFIG_PATH.exists(), "миграция должна записать файл")
        self.assertEqual(cfg["roles"]["voice"]["model"], "glm-9")
        self.assertEqual(cfg["roles"]["evaluator"]["model"], "glm-mini")
        self.assertEqual(cfg["frameworks"]["anthropic"]["api_key"], "k1")
        self.assertEqual(llm.limits().max_tool_iters, 7)

    def test_normalization_removes_legacy_behavior_limits(self):
        cfg = llm._normalize({
            "limits": {
                "max_tool_iters": 31,
                "evaluator_mode": "all",
                "windows_per_day": 1,
                "drift_action": "freeze",
            },
        })
        self.assertEqual(cfg["limits"], {"max_tool_iters": 31})

    def test_reader_persists_one_way_removal_of_legacy_behavior_limits(self):
        cfg = llm._from_env()
        cfg["limits"].update({
            "evaluator_mode": "all", "drift_soft": 3, "drift_hard": 5,
            "windows_per_day": 1,
        })
        llm.CONFIG_PATH.write_text(json.dumps(cfg), encoding="utf-8")
        loaded = llm._config()
        stored = json.loads(llm.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(loaded["limits"], {"max_tool_iters": 20})
        self.assertEqual(stored["limits"], {"max_tool_iters": 20})

    def test_vision_model_survives_normalize(self):
        cfg = llm._normalize({
            "roles": {"voice": {
                "framework": "anthropic", "model": "glm-5.3",
                "vision_model": "glm-5.3-flash",
            }},
        })
        self.assertEqual(cfg["roles"]["voice"]["vision_model"], "glm-5.3-flash")

    def test_stamp_distinguishes_same_size_replacement_even_when_clock_collides(self):
        """Atomic panel writes must reload even if filesystem time is coarsened.

        Model identifiers ``alpha`` and ``bravo`` serialize to the same byte count.
        Preserve the old mtime explicitly to model the observed filesystem tick;
        ``save_config`` replaces the file, so its identity is the remaining
        independent witness.
        """
        cfg = self._write_cfg(voice={"framework": "openai", "model": "alpha"})
        before = llm._config_stamp()
        cfg2 = json.loads(json.dumps(cfg))
        cfg2["roles"]["voice"].update(model="bravo", framework="openai")
        llm.save_config(cfg2)
        os.utime(llm.CONFIG_PATH, ns=(before[0], before[0]))
        self.assertNotEqual(before, llm._config_stamp(),
                            "atomic replacement must remain visible without clock resolution")
        fresh = llm._config()
        self.assertEqual(fresh["roles"]["voice"]["model"], "bravo")
        jr = "".join(p.read_text(encoding="utf-8") for p in llm.JOURNAL_DIR.glob("*.md"))
        self.assertIn("мозг сменился", jr)

    def test_mtime_hot_reload_and_brain_change_journal(self):
        cfg = self._write_cfg()
        # другой процесс (панель) переписал файл: save_config кэша НЕ трогает
        cfg2 = json.loads(json.dumps(cfg))
        cfg2["roles"]["voice"].update(model="gpt-9", framework="openai")
        llm.save_config(cfg2)
        llm._CACHE["mtime"] = None  # гарантированная инвалидация (mtime-тик Windows ~15мс)
        fresh = llm._config()
        self.assertEqual(fresh["roles"]["voice"]["model"], "gpt-9")
        jr = "".join(p.read_text(encoding="utf-8") for p in llm.JOURNAL_DIR.glob("*.md"))
        self.assertIn("мозг сменился", jr)
        self.assertIn("голос", jr)
        self.assertIn("gpt-9", jr)

    def test_writer_update_is_immediately_visible(self):
        self._write_cfg()
        llm.update_config({"limits": {"max_tool_iters": 33}})
        llm.update_config({"limits": {"max_tool_iters": 44}})  # тот же mtime-тик — не важно
        self.assertEqual(llm.limits().max_tool_iters, 44)

    def test_config_not_in_git(self):
        """Ключи мозга не уезжают в коммит — и правило обязано жить В РЕПО.

        ⚠ 03.08.2026: прежняя редакция принимала ЛЮБОЙ источник игнора. `git
        check-ignore` возвращает 0 и тогда, когда правило пришло из `core.excludesFile`
        пользователя или из `.git/info/exclude`, — а обе записи лежат вне репозитория и
        не переживают ни клон, ни чужую машину. Тест остался бы зелёным и в тот день,
        когда строка про llm.json пропала бы из `.gitignore` самого репо. Спрашиваем
        ИСТОЧНИК, а не факт, и отдельно — что файл ещё не отслеживается (на tracked
        файл .gitignore не действует вовсе).

        Цена ошибки здесь не «красный прогон», а ключи мозга в истории git.
        """
        if not (llm.REPO / ".git").exists():
            self.skipTest("нет git-репозитория")
        r = subprocess.run(["git", "-C", str(llm.REPO), "check-ignore", "-v",
                            "memory/llm.json"],
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, "memory/llm.json обязан быть в .gitignore")
        source = (r.stdout or "").split(":", 1)[0].strip()
        self.assertTrue(source.endswith(".gitignore"),
                        f"правило игнора пришло из {source!r}, а не из .gitignore репо — "
                        "такая защита не переживёт клон")
        tracked = subprocess.run(
            ["git", "-C", str(llm.REPO), "ls-files", "--error-unmatch", "memory/llm.json"],
            capture_output=True, text=True, timeout=20)
        self.assertNotEqual(tracked.returncode, 0,
                            "файл уже отслеживается git — .gitignore на него не действует")

    def test_broken_json_keeps_last_good(self):
        self._write_cfg(voice={"model": "glm-good"})
        llm.CONFIG_PATH.write_text("{broken", encoding="utf-8")
        cfg = llm._config()
        self.assertEqual(cfg["roles"]["voice"]["model"], "glm-good")


class TestTranslation(Base):
    def test_anthropic_response_preserves_provider_reported_model(self):
        """Каталог/запрос — не свидетель фактической модели: z.ai может подменить её в 200."""
        resp = llm._call_anthropic(
            FakeAnthropic([FakeAnthResp("ок", model="glm-5.3")]),
            "glm-5.2", system=None, messages=[], tools=None,
            max_tokens=16, thinking=None)
        self.assertEqual(resp.model, "glm-5.3")

    def test_anthropic_response_without_model_falls_back_to_requested_name(self):
        """Сервер без поля model не стирает единственное доступное имя."""
        resp = llm._call_anthropic(
            FakeAnthropic([FakeAnthResp("ок")]),
            "glm-5.2", system=None, messages=[], tools=None,
            max_tokens=16, thinking=None)
        self.assertEqual(resp.model, "glm-5.2")

    def test_anthropic_actual_model_reaches_usage_telemetry(self):
        """Нормализация должна донести resp.model до счётчиков, а не только до ответа."""
        self._write_cfg(voice={"framework": "anthropic", "model": "glm-5.2"})
        llm.use_test_client(FakeAnthropic([FakeAnthResp("ок", model="glm-5.3")]))
        with mock.patch.object(llm, "_usage_add") as usage_add, \
                mock.patch.object(llm, "_brain_note"), \
                mock.patch.object(llm, "_call_trace"):
            resp = llm.chat("voice", messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(resp.model, "glm-5.3")
        self.assertEqual(usage_add.call_args.kwargs["model"], "glm-5.3")

    def test_system_blocks_to_text(self):
        blocks = [{"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}},
                  {"type": "text", "text": "b"}]
        self.assertEqual(llm.system_text(blocks), "a\n\nb")
        self.assertEqual(llm.system_text("plain"), "plain")

    def test_tools_to_openai_keeps_native_and_drops_foreign_server_tools(self):
        tools = [{"name": "recall", "description": "поиск",
                  "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}},
                 {"type": "web_search_20250305", "name": "web_search", "max_uses": 5},
                 {"type": "web_search", "search_context_size": "medium",
                  "external_web_access": True, "max_uses": 5}]
        out = llm.tools_to_openai(tools)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["type"], "function")
        self.assertEqual(out[0]["function"]["name"], "recall")
        self.assertIn("properties", out[0]["function"]["parameters"])
        self.assertEqual(out[1], {"type": "web_search", "search_context_size": "medium",
                                  "external_web_access": True, "max_uses": 5})

    def test_tools_to_anthropic_drops_relay_hosted_search(self):
        fn = {"name": "recall", "input_schema": {"type": "object"}}
        zai = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
        native = {"type": "web_search", "external_web_access": True}
        result = llm.tools_to_anthropic([fn, zai, native])
        # relay-only web_search отфильтрован; остались fn и zai
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], fn)
        # последний tool получает cache_control (если PRAXIS_PROMPT_CACHE != 0)
        import os as _os
        if _os.getenv("PRAXIS_PROMPT_CACHE", "1").lower() not in ("0", "false", "no"):
            self.assertEqual(result[-1].get("cache_control"), {"type": "ephemeral"})
            # и при этом исходные ключи сохранены
            self.assertEqual(result[-1].get("name"), "web_search")
        else:
            self.assertEqual(result, [fn, zai])

    def test_tools_to_anthropic_cache_disabled(self):
        """PRAXIS_PROMPT_CACHE=0 — tools уходят без cache_control."""
        import os as _os
        fn = {"name": "recall", "input_schema": {"type": "object"}}
        old_val = _os.environ.get("PRAXIS_PROMPT_CACHE")
        _os.environ["PRAXIS_PROMPT_CACHE"] = "0"
        try:
            result = llm.tools_to_anthropic([fn])
            self.assertEqual(result, [fn])
            self.assertNotIn("cache_control", result[0])
        finally:
            if old_val is None:
                _os.environ.pop("PRAXIS_PROMPT_CACHE", None)
            else:
                _os.environ["PRAXIS_PROMPT_CACHE"] = old_val

    def test_native_search_options_are_strict(self):
        with self.assertRaises(ValueError):
            llm.tools_to_openai([{"type": "web_search", "search_context_size": "huge"}])
        with self.assertRaises(ValueError):
            llm.tools_to_openai([{"type": "web_search", "external_web_access": "yes"}])
        with self.assertRaises(ValueError):
            llm.tools_to_openai([{"type": "web_search", "max_uses": 0}])

    def test_tools_to_openai_sets_additional_properties_false(self):
        # 400 invalid_function_parameters ловили на recall (tools[0]) — openai-протокол
        # (в т.ч. codex-relay) требует additionalProperties:false на параметрах функции.
        tools = [{"name": "recall", "description": "поиск",
                  "input_schema": {"type": "object", "properties": {"q": {"type": "string"}},
                                   "required": ["q"]}}]
        out = llm.tools_to_openai(tools)
        params = out[0]["function"]["parameters"]
        self.assertIs(params["additionalProperties"], False)
        self.assertEqual(params["required"], ["q"], "остальная схема не искажается")

    def test_tools_to_openai_missing_schema_still_gets_flag(self):
        out = llm.tools_to_openai([{"name": "x", "description": "", "input_schema": None}])
        self.assertIs(out[0]["function"]["parameters"]["additionalProperties"], False)

    def test_tools_to_openai_nested_object_and_array_get_flag_too(self):
        tools = [{"name": "y", "description": "", "input_schema": {
            "type": "object",
            "properties": {
                "filt": {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
                "items": {"type": "array", "items": {"type": "object",
                          "properties": {"b": {"type": "string"}}}},
            },
            "required": ["filt"]}}]  # filt required (anthropic-side), items optional
        out = llm.tools_to_openai(tools)
        p = out[0]["function"]["parameters"]
        self.assertIs(p["additionalProperties"], False)
        self.assertEqual(set(p["required"]), {"filt", "items"}, "required расширен до всех ключей")
        # filt был required — не оборачивается в anyOf, остаётся прямой строгой object-схемой
        self.assertIs(p["properties"]["filt"]["additionalProperties"], False)
        # items НЕ был required — обёрнут в anyOf[<строгая схема>, null]
        self.assertEqual(p["properties"]["items"]["anyOf"][1], {"type": "null"})
        self.assertIs(p["properties"]["items"]["anyOf"][0]["items"]["additionalProperties"], False)

    def test_tools_to_openai_required_includes_all_keys(self):
        # 400 invalid_function_parameters ловили на remember (tools[1]): open_loop и другие
        # опциональные поля не входили в required — strict-mode этого не прощает.
        tools = [{"name": "remember", "description": "", "input_schema": {
            "type": "object",
            "properties": {
                "person": {"type": "string"},
                "fact": {"type": "string"},
                "visibility": {"type": "string", "enum": ["public", "private"]},
                "salience": {"type": "integer", "enum": [1, 2, 3]},
                "open_loop": {"type": "boolean"},
            },
            "required": ["person", "fact"]}}]
        out = llm.tools_to_openai(tools)
        p = out[0]["function"]["parameters"]
        self.assertEqual(set(p["required"]), {"person", "fact", "visibility", "salience", "open_loop"})
        # изначально обязательные — без anyOf-обёртки, схема как есть
        self.assertEqual(p["properties"]["person"], {"type": "string"})
        self.assertEqual(p["properties"]["fact"], {"type": "string"})
        # изначально необязательные — anyOf с null-веткой, исходная схема сохранена внутри
        self.assertEqual(p["properties"]["open_loop"]["anyOf"], [{"type": "boolean"}, {"type": "null"}])
        self.assertEqual(p["properties"]["visibility"]["anyOf"][0],
                         {"type": "string", "enum": ["public", "private"]})

    def test_strict_schema_does_not_mutate_original(self):
        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        llm._openai_strict_schema(schema)
        self.assertNotIn("additionalProperties", schema, "исходная anthropic-схема не должна портиться")

    def test_messages_roundtrip_tool_use(self):
        messages = [
            {"role": "user", "content": "найди"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "ищу"},
                {"type": "tool_use", "id": "t1", "name": "recall", "input": {"q": "горы"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "нашла: горы"}]},
        ]
        out = llm.messages_to_openai(messages)
        self.assertEqual(out[0], {"role": "user", "content": "найди"})
        self.assertEqual(out[1]["role"], "assistant")
        self.assertEqual(out[1]["tool_calls"][0]["function"]["name"], "recall")
        self.assertEqual(json.loads(out[1]["tool_calls"][0]["function"]["arguments"]), {"q": "горы"})
        self.assertEqual(out[2], {"role": "tool", "tool_call_id": "t1", "content": "нашла: горы"})

    def test_image_block_maps_to_openai_data_url_and_anthropic_source(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "pixel.png"
            p.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            messages = [{"role": "user", "content": [
                {"type": "text", "text": "что здесь?"},
                {"type": "image", "path": str(p), "mime": "image/png", "detail": "original"},
            ]}]
            oa = llm.messages_to_openai(messages)[0]["content"]
            self.assertEqual(oa[0], {"type": "text", "text": "что здесь?"})
            self.assertEqual(oa[1]["type"], "image_url")
            self.assertTrue(oa[1]["image_url"]["url"].startswith("data:image/png;base64,"))
            self.assertEqual(oa[1]["image_url"]["detail"], "original")
            anth = llm.messages_to_anthropic(messages)[0]["content"][1]
            self.assertEqual(anth["source"]["type"], "base64")
            self.assertEqual(anth["source"]["media_type"], "image/png")

    def test_image_block_rejects_unsupported_format(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "photo.heic"
            p.write_bytes(b"fixture")
            with self.assertRaisesRegex(ValueError, "формат изображения"):
                llm.messages_to_openai([{"role": "user", "content": [
                    {"type": "image", "path": str(p), "mime": "image/heic"}]}])

    def test_blocks_from_openai_parses_arguments(self):
        tc = types.SimpleNamespace(id="c1", function=types.SimpleNamespace(
            name="remember", arguments='{"person": "Вика", "fact": "коллега"}'))
        msg = types.SimpleNamespace(content="запишу", tool_calls=[tc])
        blocks = llm.blocks_from_openai(msg)
        self.assertEqual(blocks[0], {"type": "text", "text": "запишу"})
        self.assertEqual(blocks[1]["type"], "tool_use")
        self.assertEqual(blocks[1]["input"], {"person": "Вика", "fact": "коллега"})

    def test_blocks_from_openai_marks_unparsable_arguments(self):
        """Обрыв длинных `arguments` больше не выглядит как вызов без аргументов.

        Здесь стояло `args = {}` — и для руки, у которой все параметры опциональные
        (`check_email`, `my_agenda`, `recent_turns`), это было не ошибкой, а ДРУГИМ
        действием с дефолтами: намерение модели исчезало без следа.
        """
        tc = types.SimpleNamespace(id="c1", function=types.SimpleNamespace(
            name="say", arguments='{"text": "привет'))
        blocks = llm.blocks_from_openai(
            types.SimpleNamespace(content=None, tool_calls=[tc]))
        self.assertEqual(blocks[0]["input"],
                         {llm.MALFORMED_JSON_KEY: '{"text": "привет'})
        self.assertTrue(llm.is_malformed_json_input(blocks[0]["input"]))
        self.assertEqual(blocks[0]["name"], "say", "имя руки обязано выжить")

    def test_the_mark_keeps_only_a_diagnostic_head_of_the_raw_arguments(self):
        long_tc = types.SimpleNamespace(id="c2", function=types.SimpleNamespace(
            name="say", arguments='{"text": "' + "я" * 5000))
        blocks = llm.blocks_from_openai(
            types.SimpleNamespace(content=None, tool_calls=[long_tc]))
        self.assertEqual(len(blocks[0]["input"][llm.MALFORMED_JSON_KEY]),
                         llm.MALFORMED_JSON_KEEP)

    def test_empty_arguments_stay_an_honest_empty_call(self):
        """Пустая строка аргументов — это «без аргументов», а не битый JSON."""
        calls = [types.SimpleNamespace(id=f"c{i}", function=types.SimpleNamespace(
            name="my_agenda", arguments=raw))
            for i, raw in enumerate(("", "   ", "{}"))]
        calls.append(types.SimpleNamespace(id="c9", function=types.SimpleNamespace(
            name="my_agenda", arguments="[1, 2]")))
        blocks = llm.blocks_from_openai(
            types.SimpleNamespace(content=None, tool_calls=calls))
        for block in blocks[:3]:
            self.assertEqual(block["input"], {})
            self.assertFalse(llm.is_malformed_json_input(block["input"]))
        self.assertEqual(blocks[3]["input"], {llm.MALFORMED_JSON_KEY: "[1, 2]"},
                         "валидный JSON, но не объект — тоже непрочитанное намерение")

    def test_openai_call_maps_stop_reason_and_system(self):
        self._write_cfg(voice={"framework": "openai", "model": "gpt-x"})
        tc = types.SimpleNamespace(id="c1", function=types.SimpleNamespace(
            name="recall", arguments='{"q": "x"}'))
        fo = FakeOpenAI(_openai_resp(text=None, tool_calls=[tc], finish="tool_calls"))
        llm.use_test_client(fo, "openai")
        resp = llm.chat("voice", system=[{"type": "text", "text": "sys",
                                          "cache_control": {"type": "ephemeral"}}],
                        messages=[{"role": "user", "content": "hi"}],
                        tools=[{"name": "recall", "description": "", "input_schema": {"type": "object"}},
                               {"type": "web_search", "search_context_size": "medium",
                                "external_web_access": True, "max_uses": 5}])
        self.assertEqual(resp.stop_reason, "tool_use")
        self.assertEqual(resp.blocks[0]["name"], "recall")
        sent = fo.calls[0]["messages"]
        self.assertEqual(sent[0], {"role": "system", "content": "sys"})
        self.assertEqual(fo.calls[0]["tools"][1], {
            "type": "web_search", "search_context_size": "medium",
            "external_web_access": True, "max_uses": 5})
        self.assertEqual(resp.usage, {"in": 7, "out": 2})

    def test_openai_thinking_maps_to_reasoning_effort_tiers(self):
        # relay по умолчанию гасит reasoning (effort=none); явный thinking-бюджет
        # должен возвращать глубину через per-request reasoning_effort
        self._write_cfg(voice={"framework": "openai", "model": "gpt-x"})
        fo = FakeOpenAI()
        llm.use_test_client(fo, "openai")
        llm.chat("voice", messages=[{"role": "user", "content": "hi"}], thinking=4096)
        self.assertEqual(fo.calls[0]["extra_body"], {"reasoning_effort": "medium"})
        llm.chat("voice", messages=[{"role": "user", "content": "hi"}])
        self.assertNotIn("extra_body", fo.calls[1])
        self.assertEqual(llm._openai_reasoning_effort(None), None)
        self.assertEqual(llm._openai_reasoning_effort(1024), "low")
        self.assertEqual(llm._openai_reasoning_effort(50000), "high")


def _blk(type_, **kw):
    return types.SimpleNamespace(type=type_, **kw)


class TestWebSearchBlockLeak(unittest.TestCase):
    """z.ai's web_search_20250305 не ведёт себя как настоящий Anthropic-сервер-тул: одним ответом
    приходит [text(наррация вызова), server_tool_use, text(сырой JSON результата), tool_result,
    text(финальный чистый ответ)] — три text-блока, два из них мусор. Живой пример (06.07,
    Bitcoin-запрос через glm-5.2): именно эта форма и породила утечку 05.07 (см. self.md)."""

    def _live_shape(self):
        return [
            _blk("text", text="**🔍 Z.ai Built-in Tool: web_search_prime**\n\n**Input:**\n```json\n"
                               "{\"search_query\":\"x\"}\n```\n*Executing on server...*\n"),
            _blk("server_tool_use", id="c1", name="web_search_prime", input={"search_query": "x"}),
            _blk("text", text="**Output:**\n**web_search_prime_result_summary:** "
                               "[{\"title\": \"...\", \"link\": \"https://x\", \"content\": \"...\"}]"),
            _blk("tool_result", tool_use_id="c1", content="[...]"),
            _blk("text", text="Bitcoin is trading around $63,000 today."),
        ]

    def test_only_final_text_block_survives(self):
        resp = types.SimpleNamespace(content=self._live_shape())
        blocks = llm._blocks_from_anthropic(resp)
        self.assertEqual(blocks, [{"type": "text", "text": "Bitcoin is trading around $63,000 today."}])

    def test_text_of_matches(self):
        resp = types.SimpleNamespace(content=self._live_shape())
        self.assertEqual(llm.text_of(resp), "Bitcoin is trading around $63,000 today.")

    def test_no_opaque_blocks_keeps_all_text_as_before(self):
        # обычный ответ (несколько text-блоков, БЕЗ server_tool_use/tool_result) — поведение
        # не меняется: всё склеивается, как раньше (никакого "opaque" маркера — нечего резать)
        resp = types.SimpleNamespace(content=[_blk("text", text="a"), _blk("text", text="b")])
        self.assertEqual(llm.text_of(resp), "ab")

    def test_client_tool_use_flow_unaffected(self):
        # обычный recall/remember и т.п.: text(преамбула) + tool_use — единственный "opaque" тип,
        # который код ВСЕГДА понимал (tool_use), не режется как web-search-мусор
        resp = types.SimpleNamespace(content=[
            _blk("text", text="ищу в памяти"),
            _blk("tool_use", id="t1", name="recall", input={"query": "горы"}),
        ])
        blocks = llm._blocks_from_anthropic(resp)
        self.assertEqual(blocks, [
            {"type": "text", "text": "ищу в памяти"},
            {"type": "tool_use", "id": "t1", "name": "recall", "input": {"query": "горы"}},
        ])

    def test_text_before_and_after_tool_result_both_dropped_before_last(self):
        # если опаковых блоков несколько — режем всё до ПОСЛЕДНЕГО, не до первого
        resp = types.SimpleNamespace(content=[
            _blk("text", text="мусор 1"),
            _blk("server_tool_use", id="c1", name="x", input={}),
            _blk("text", text="мусор 2"),
            _blk("tool_result", tool_use_id="c1", content="..."),
            _blk("server_tool_use", id="c2", name="y", input={}),
            _blk("tool_result", tool_use_id="c2", content="..."),
            _blk("text", text="настоящий ответ"),
        ])
        self.assertEqual(llm.text_of(resp), "настоящий ответ")


class TestAddressedModel(Base):
    def test_cross_framework_override_preserves_configured_fallback_association(self):
        cfg = llm._from_env()
        cfg["frameworks"]["openai"]["api_key"] = "sk-test"
        cfg["frameworks"]["anthropic"]["api_key"] = "zai-test"
        cfg["roles"]["voice"].update(framework="openai", model="gpt-primary",
                                     fallback_model="glm-fallback", fallback_framework="")
        llm.save_config(cfg)
        failure = RateLimitError("429")
        calls = []

        def call(framework, model, **kwargs):
            calls.append((framework, model))
            if model == "glm-requested":
                raise failure
            return llm.LLMResponse(text="fallback", model=model, framework=framework)

        with mock.patch.object(llm, "_available_models", side_effect=lambda fw:
                ["glm-requested", "glm-fallback"] if fw == "anthropic" else ["gpt-primary"]), \
                mock.patch.object(llm, "_client_for", return_value=object()), \
                mock.patch.object(llm, "_call", side_effect=call):
            response = llm.chat("voice", model="glm-requested", messages=[])
        self.assertEqual(calls, [("anthropic", "glm-requested"), ("anthropic", "glm-fallback")])
        self.assertEqual(response.model, "glm-fallback")
        self.assertEqual(llm._config()["roles"]["voice"]["model"], "gpt-primary")

    def test_override_uses_requested_model_without_mutating_voice_config(self):
        cfg = llm._from_env()
        cfg["frameworks"]["openai"]["api_key"] = "sk-test"
        cfg["roles"]["voice"].update(framework="openai", model="gpt-5.6-sol")
        llm.save_config(cfg)
        client = FakeOpenAI(_openai_resp(text="terra answer"))
        llm.use_test_client(client, "openai")
        with mock.patch.object(llm, "_available_models",
                               side_effect=lambda fw: ["gpt-5.6-sol", "gpt-5.6-terra"]
                               if fw == "openai" else []):
            resp = llm.chat("voice", model="gpt-5.6-terra",
                            messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(client.calls[0]["model"], "gpt-5.6-terra")
        self.assertEqual(resp.model, "gpt-5.6-terra")
        self.assertEqual(llm._config()["roles"]["voice"]["model"], "gpt-5.6-sol")

    def test_override_can_select_the_other_framework_without_mutating_voice_config(self):
        cfg = llm._from_env()
        cfg["frameworks"]["anthropic"]["api_key"] = "zai-key"
        cfg["frameworks"]["openai"]["api_key"] = "sk-test"
        cfg["roles"]["voice"].update(framework="anthropic", model="glm-5.2")
        llm.save_config(cfg)
        client = FakeOpenAI(_openai_resp(text="terra answer"))
        llm.use_test_client(client, "openai")
        with mock.patch.object(llm, "_available_models",
                               side_effect=lambda fw: ["gpt-5.6-terra"]
                               if fw == "openai" else ["glm-5.2"]):
            resp = llm.chat("voice", model="gpt-5.6-terra",
                            messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(resp.framework, "openai")
        self.assertEqual(client.calls[0]["model"], "gpt-5.6-terra")
        self.assertEqual(llm._config()["roles"]["voice"]["framework"], "anthropic")
        self.assertEqual(llm._config()["roles"]["voice"]["model"], "glm-5.2")

    def test_unknown_override_fails_before_provider_call(self):
        cfg = llm._from_env()
        cfg["frameworks"]["openai"]["api_key"] = "sk-test"
        cfg["roles"]["voice"].update(framework="openai", model="gpt-5.6-sol")
        llm.save_config(cfg)
        client = FakeOpenAI()
        llm.use_test_client(client, "openai")
        with mock.patch.object(llm, "_available_models", return_value=[]):
            with self.assertRaisesRegex(ValueError, "отсутствует в доступном каталоге"):
                llm.chat("voice", model="invented-model",
                         messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(client.calls, [])


class TestFallback(Base):
    def _arm(self):
        cfg = llm._from_env()
        cfg["frameworks"]["anthropic"]["api_key"] = "zai-key"
        cfg["frameworks"]["openai"]["api_key"] = "sk-test"
        cfg["roles"]["voice"].update(fallback_model="gpt-fb")
        llm.save_config(cfg)

    def test_429_falls_back_and_journals(self):
        self._arm()
        llm.use_test_client(FakeAnthropic(error=RateLimitError("429 Insufficient balance")))
        fo = FakeOpenAI(_openai_resp(text="запасной ответ"))
        llm.use_test_client(fo, "openai")
        resp = llm.chat("voice", messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(resp.text, "запасной ответ")
        self.assertEqual(resp.framework, "openai")
        self.assertEqual(fo.calls[0]["model"], "gpt-fb")
        self.assertTrue(llm.snapshot()["voice"]["on_fallback"])
        jr = "".join(p.read_text(encoding="utf-8") for p in llm.JOURNAL_DIR.glob("*.md"))
        self.assertIn("фолбэк", jr)
        self.assertIn("RateLimitError", jr)
        self.assertNotIn("zai-key", jr, "ключей в дневнике быть не должно")

    def test_primary_success_resets_flag(self):
        self._arm()
        llm.use_test_client(FakeAnthropic(error=RateLimitError("429")))
        llm.use_test_client(FakeOpenAI(), "openai")
        llm.chat("voice", messages=[{"role": "user", "content": "hi"}])
        self.assertTrue(llm.snapshot()["voice"]["on_fallback"])
        llm.use_test_client(FakeAnthropic([FakeAnthResp("ожила")]))
        resp = llm.chat("voice", messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(resp.text, "ожила")
        self.assertFalse(llm.snapshot()["voice"]["on_fallback"])

    def test_no_fallback_model_reraises(self):
        cfg = llm._from_env()
        cfg["frameworks"]["anthropic"]["api_key"] = "zai-key"
        cfg["frameworks"]["openai"]["api_key"] = "sk-test"
        llm.save_config(cfg)  # fallback_model пуст
        llm.use_test_client(FakeAnthropic(error=RateLimitError("429")))
        llm.use_test_client(FakeOpenAI(), "openai")
        with self.assertRaises(RateLimitError):
            llm.chat("voice", messages=[{"role": "user", "content": "hi"}])

    def test_non_fallbackable_error_reraises(self):
        self._arm()
        llm.use_test_client(FakeAnthropic(error=ValueError("bad request")))
        llm.use_test_client(FakeOpenAI(), "openai")
        with self.assertRaises(ValueError):
            llm.chat("voice", messages=[{"role": "user", "content": "hi"}])

    def _arm_openai_primary(self):
        # голос НА openai (свопнут на relay), фолбэк — anthropic/glm
        cfg = llm._from_env()
        cfg["frameworks"]["anthropic"]["api_key"] = "zai-key"
        cfg["frameworks"]["openai"]["api_key"] = "sk-test"
        cfg["roles"]["voice"].update(framework="openai", model="gpt-5.5", fallback_model="glm-fb")
        llm.save_config(cfg)

    def test_empty_openai_stream_falls_back_to_anthropic(self):
        # живой баг 06.07: relay/gpt-5.5 отдавал пустой стрим (ни текста, ни тула) 10/10 — это
        # не «end_turn с пустым ответом», а сбой канала; голос должен уйти на glm, а не молчать
        self._arm_openai_primary()
        empty = [_chunk(finish="stop",
                        usage=types.SimpleNamespace(prompt_tokens=3, completion_tokens=0))]
        llm.use_test_client(FakeStreamOpenAI(empty), "openai")
        llm.use_test_client(FakeAnthropic([FakeAnthResp("живой ответ с glm")]))
        resp = llm.chat("voice", messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(resp.text, "живой ответ с glm")
        self.assertEqual(resp.framework, "anthropic")
        self.assertTrue(llm.snapshot()["voice"]["on_fallback"])

    def test_error_content_openai_stream_falls_back(self):
        # relay встраивает апстрим-4xx как content с finish_reason='error' («Error: 400 - …») —
        # раньше это утекало в чат как её реплика; теперь трактуется как сбой → фолбэк на glm
        self._arm_openai_primary()
        errchunk = [_chunk(content="Error: 400 Bad Request - {invalid schema}", finish="error")]
        llm.use_test_client(FakeStreamOpenAI(errchunk), "openai")
        llm.use_test_client(FakeAnthropic([FakeAnthResp("ответ с glm")]))
        resp = llm.chat("voice", messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(resp.text, "ответ с glm")
        self.assertEqual(resp.framework, "anthropic")
        self.assertNotIn("Error: 400", resp.text, "сырая ошибка релея не должна стать репликой")

    def test_same_framework_fallback_goes_through_the_same_relay(self):
        # 17.08.2026: fallback_framework="openai" при framework=openai — фолбэк ЧЕРЕЗ ТО ЖЕ
        # реле другой моделью (terra → luna). Обрывы апстрима спорадические (2,1% на terra),
        # повтор другой моделью почти всегда проходит — вторая подписка не нужна.
        # Это не «повтор поверх сказанного»: модель другая, канал тот же.
        cfg = llm._from_env()
        cfg["frameworks"]["openai"]["api_key"] = "sk-test"
        cfg["roles"]["voice"].update(framework="openai", model="gpt-terra",
                                     fallback_model="gpt-luna",
                                     fallback_framework="openai")
        llm.save_config(cfg)

        class TerraTearsLunaAnswers(FakeStreamOpenAI):
            """terra пуста ВСЕГДА (и первый вызов, и повтор по своему каналу),
            отвечает только luna — иначе ретрай той же моделью съедает сценарий
            раньше фолбэка, и тест меряет не то."""
            def create(self, **kw):
                self.calls.append(kw)
                if kw.get("model") == "gpt-terra":
                    return iter([_chunk(finish="stop")])
                return iter([_chunk(content="лунный ответ", finish="stop")])

        fo = TerraTearsLunaAnswers([])
        llm.use_test_client(fo, "openai")
        resp = llm.chat("voice", messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(resp.text, "лунный ответ")
        self.assertEqual(resp.framework, "openai", "фолбэк остался на том же фреймворке")
        self.assertEqual(fo.calls[0]["model"], "gpt-terra")
        self.assertEqual(fo.calls[-1]["model"], "gpt-luna",
                         "после пустой терры (со всеми её ретраями) ответ обязан прийти луной")
        self.assertTrue(llm.snapshot()["voice"]["on_fallback"])

    def test_missing_same_framework_fallback_slug_cannot_rotate_back_to_primary(self):
        # Живой конфиг 03.09: openai/sol + fallback openai/glm-5.3. Relay не знает
        # glm, поэтому обычная ротация имени выбирала sol и называла повтор той же
        # модели fallback'ом. При наличии terra обязаны уйти именно на неё.
        cfg = llm._from_env()
        cfg["frameworks"]["openai"]["api_key"] = "sk-test"
        cfg["roles"]["voice"].update(
            framework="openai", model="gpt-5.6-sol",
            fallback_model="glm-5.3", fallback_framework="openai",
        )
        llm.save_config(cfg)

        class SolTearsTerraAnswers(FakeStreamOpenAI):
            def create(self, **kw):
                self.calls.append(kw)
                if kw.get("model") == "gpt-5.6-sol":
                    return iter([_chunk(finish="stop")])
                return iter([_chunk(content="ответ терры", finish="stop")])

        fo = SolTearsTerraAnswers([])
        llm.use_test_client(fo, "openai")
        models = ["gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]
        with mock.patch.object(llm, "_available_models", return_value=models):
            resp = llm.chat("voice", messages=[{"role": "user", "content": "hi"}])

        self.assertEqual(resp.text, "ответ терры")
        self.assertEqual(fo.calls[-1]["model"], "gpt-5.6-terra")
        self.assertEqual(sum(c["model"] == "gpt-5.6-sol" for c in fo.calls),
                         llm.EMPTY_RETRIES + 1,
                         "primary получает только положенные empty-retry, не ложный fallback")

    def test_same_framework_fallback_fails_closed_when_only_primary_is_live(self):
        cfg = llm._from_env()
        cfg["frameworks"]["openai"]["api_key"] = "sk-test"
        cfg["roles"]["voice"].update(
            framework="openai", model="gpt-5.6-sol",
            fallback_model="glm-5.3", fallback_framework="openai",
        )
        llm.save_config(cfg)
        fo = FakeStreamOpenAI([_chunk(finish="stop")])
        llm.use_test_client(fo, "openai")

        with mock.patch.object(llm, "_available_models", return_value=["gpt-5.6-sol"]):
            with self.assertRaises(llm.EmptyResponseError):
                llm.chat("voice", messages=[{"role": "user", "content": "hi"}])

        self.assertEqual(len(fo.calls), llm.EMPTY_RETRIES + 1,
                         "при отсутствии иной модели не повторяем primary под именем fallback")
        self.assertTrue(all(c["model"] == "gpt-5.6-sol" for c in fo.calls))

    def test_fallback_framework_survives_normalize_and_arms_state(self):
        # Поле обязано пережить _normalize (роль переписывается целиком) и взводить
        # fallback_armed по НАСТОЯЩЕМУ фреймворку фолбэка, а не по противоположному.
        cfg = llm._from_env()
        cfg["frameworks"]["openai"]["api_key"] = "sk-test"
        # anthropic без ключа: старая логика считала бы фолбэк невзведённым
        cfg["roles"]["voice"].update(framework="openai", model="gpt-terra",
                                     fallback_model="gpt-luna",
                                     fallback_framework="openai")
        llm.save_config(cfg)
        rc = llm._config()["roles"]["voice"]
        self.assertEqual(rc.get("fallback_framework"), "openai")
        snap = llm.snapshot()["voice"]
        self.assertTrue(snap["fallback_armed"],
                        "same-framework фолбэк с живым ключом обязан считаться взведённым")

    def test_empty_after_spoken_gets_one_retry_then_ends_the_turn(self):
        # Контракт v3, её решение №3: пустота — всегда аномалия с ОДНИМ ретраем своим
        # каналом; после сказанного второй пустой ответ — конец хода, не фолбэк.
        # 17.08 без этого пустота терры уходила в фолбэк, и луна слала ту же реплику
        # заново — четыре копии за две минуты.
        self._arm_openai_primary()  # фолбэк ВЗВЕДЁН — и всё равно не смеет сработать
        fo = FakeStreamOpenAI([_chunk(finish="stop")])
        llm.use_test_client(fo, "openai")
        fa = FakeAnthropic([FakeAnthResp("не должна прозвучать")])
        llm.use_test_client(fa)
        resp = llm.chat("voice", messages=[{"role": "user", "content": "hi"}],
                        end_after_spoken=True)
        self.assertEqual(resp.text, "")
        self.assertEqual(resp.stop_reason, "end_turn")
        self.assertEqual(len(fo.calls), 2,
                         "пустоте положен РОВНО ОДИН ретрай своим каналом (её решение №3)")
        self.assertEqual(getattr(fa, "calls", []), [],
                         "фолбэк переисполнил уже принятое решение — петля 17.08 не закрыта")
        self.assertFalse(llm.snapshot()["voice"]["on_fallback"])

    def test_a_torn_stream_after_spoken_also_ends_instead_of_fallback(self):
        # v3: после доставленной реплики ЛЮБАЯ смерть канала закрывает ход сказанным.
        # Анти-повтор руки держит только байт-в-байт копию; вторая модель могла бы
        # сказать ту же мысль другими словами — и для человека это снова дубль.
        self._arm_openai_primary()
        errchunk = [_chunk(content="Error: 400 Bad Request - {invalid}", finish="error")]
        llm.use_test_client(FakeStreamOpenAI(errchunk), "openai")
        fa = FakeAnthropic([FakeAnthResp("не должна прозвучать")])
        llm.use_test_client(fa)
        resp = llm.chat("voice", messages=[{"role": "user", "content": "hi"}],
                        end_after_spoken=True)
        self.assertEqual(resp.text, "", "обрыв после сказанного обязан закрывать ход")
        self.assertEqual(getattr(fa, "calls", []), [],
                         "фолбэк после доставки — переисполнение решения")

    def test_a_torn_stream_before_delivery_still_falls_back(self):
        # Граница флага: ДО первой доставки фолбэк — страховка доставки, он остаётся.
        self._arm_openai_primary()
        errchunk = [_chunk(content="Error: 400 Bad Request - {invalid}", finish="error")]
        llm.use_test_client(FakeStreamOpenAI(errchunk), "openai")
        llm.use_test_client(FakeAnthropic([FakeAnthResp("живой ответ с glm")]))
        resp = llm.chat("voice", messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(resp.text, "живой ответ с glm",
                         "обрыв до доставки перестал лечиться фолбэком")

    def test_empty_openai_stream_without_fallback_raises(self):
        # нет fallback_model — пустой ответ релея честно поднимается как ошибка (не тихая пустота)
        cfg = llm._from_env()
        cfg["frameworks"]["openai"]["api_key"] = "sk-test"
        cfg["roles"]["voice"].update(framework="openai", model="gpt-5.5")  # fallback пуст
        llm.save_config(cfg)
        llm.use_test_client(FakeStreamOpenAI([_chunk(finish="stop")]), "openai")
        with self.assertRaises(llm.EmptyResponseError):
            llm.chat("voice", messages=[{"role": "user", "content": "hi"}])


class TestWebSearchAvailable(Base):
    """Hosted search is truthful for z.ai and explicitly capable Codex relays."""

    def setUp(self):
        super().setUp()
        self._ws0 = os.environ.get("PRAXIS_WEB_SEARCH")
        self._native0 = os.environ.get("PRAXIS_OPENAI_NATIVE_WEB_SEARCH")
        self.addCleanup(lambda: (os.environ.pop("PRAXIS_WEB_SEARCH", None) if self._ws0 is None
                                 else os.environ.__setitem__("PRAXIS_WEB_SEARCH", self._ws0)))
        self.addCleanup(lambda: (os.environ.pop("PRAXIS_OPENAI_NATIVE_WEB_SEARCH", None)
                                 if self._native0 is None else os.environ.__setitem__(
                                     "PRAXIS_OPENAI_NATIVE_WEB_SEARCH", self._native0)))
        os.environ.pop("PRAXIS_OPENAI_NATIVE_WEB_SEARCH", None)

    def test_off_by_env(self):
        self._write_cfg(voice={"framework": "anthropic"})
        os.environ.pop("PRAXIS_WEB_SEARCH", None)
        self.assertFalse(llm.web_search_available())

    def test_on_for_anthropic_or_explicitly_capable_relay(self):
        os.environ["PRAXIS_WEB_SEARCH"] = "1"
        self._write_cfg(voice={"framework": "anthropic", "model": "glm-x"})
        self.assertTrue(llm.web_search_available())
        self.assertEqual(llm.web_search_backend(), "anthropic")
        llm.update_config({"roles": {"voice": {"framework": "openai", "model": "gpt-x"}}})
        self.assertFalse(llm.web_search_available(),
                         "generic Chat Completions must not receive a Responses hosted tool")
        os.environ["PRAXIS_OPENAI_NATIVE_WEB_SEARCH"] = "1"
        self.assertTrue(llm.web_search_available())
        self.assertEqual(llm.web_search_backend(), "openai")


class TestSnapshotPing(Base):
    def test_snapshot_shape(self):
        self._write_cfg(voice={"fallback_model": "gpt-fb"})
        snap = llm.snapshot()
        for role in ("voice", "evaluator"):
            for key in ("framework", "model", "fallback_model", "fallback_armed",
                        "on_fallback", "last_error"):
                self.assertIn(key, snap[role])
        self.assertFalse(snap["voice"]["fallback_armed"], "без openai-ключа фолбэк не armed")

    def test_ping_ok_and_error(self):
        self._write_cfg()
        fake = FakeAnthropic([FakeAnthResp("pong")])
        llm.use_test_client(fake)
        ok, err = llm.ping("voice")
        self.assertTrue(ok)
        self.assertEqual(err, "")
        self.assertEqual(fake.calls[0]["max_tokens"], 1)
        self.assertNotIn("thinking", fake.calls[0],
                         "ordinary Anthropic ping must preserve the cheap legacy request")
        llm.use_test_client(FakeAnthropic(error=RateLimitError("429 dead")))
        ok, err = llm.ping("voice")
        self.assertFalse(ok)
        self.assertIn("RateLimitError", err)

    def test_ping_glm_53_enables_required_thinking(self):
        self._write_cfg(voice={"framework": "anthropic", "model": "glm-5.3"})
        fake = FakeAnthropic([FakeAnthResp("pong")])
        llm.use_test_client(fake)

        ok, err = llm.ping("voice")

        self.assertTrue(ok)
        self.assertEqual(err, "")
        self.assertEqual(fake.calls[0]["thinking"], {"type": "enabled", "budget_tokens": 1024})
        self.assertEqual(fake.calls[0]["max_tokens"], 2048,
                         "Anthropic thinking needs room for its budget plus the visible answer")

    def test_state_line_separates_configured_from_observed(self):
        """⚠ РАНЬШЕ ЗДЕСЬ ПРОВЕРЯЛОСЬ ТОЛЬКО НАСТРОЕННОЕ, И ЭТОГО ХВАТАЛО, ЧТОБЫ КАДР
        ВРАЛ ЕЙ О НЕЙ.

        Замер 08.08: конфиг, манифест рельсов и эта строка втроём говорили `gpt-5.6-sol`,
        а из ста пятидесяти ходов восемнадцать прошли на `gpt-5.6-terra`. Praxis честно
        пересказывала свой кадр — неправду говорил кадр. Её решение: показывать обе вещи,
        и «накопительная статистика не должна подменять факт последнего реально
        ответившего backend».
        """
        self._write_cfg()
        line = llm.state_line()
        self.assertIn("голос:", line)
        self.assertIn("вспомогательная:", line)
        self.assertIn("настроено=", line)
        self.assertIn("последний ответ=", line,
                      "строка снова говорит только о настроенном")

    def test_observed_is_the_fact_not_the_config(self):
        """Наблюдённое берётся из журнала ФАКТИЧЕСКИХ вызовов, а не из конфига."""
        self._write_cfg()
        with mock.patch.object(llm, "_usage_load", return_value={
            praxis_time.day_key(): {"voice": {
                "last": {"model": "совсем-другая-модель", "at": "08.08 04:00"},
                "models": {"совсем-другая-модель": {"calls": 18},
                           "настроенная": {"calls": 132}},
            }},
        }):
            last, counts = llm.observed_models("voice")
            line = llm.state_line()
        self.assertEqual(last["model"], "совсем-другая-модель")
        self.assertEqual(counts["настроенная"], 132)
        self.assertIn("совсем-другая-модель", line)
        self.assertIn("ДРУГАЯ", line, "расхождение с конфигом не названо вслух")
        self.assertIn("за сутки:", line, "счёт по моделям пропал")

    def test_no_observation_says_so_instead_of_repeating_the_config(self):
        """«Не наблюдалось» честнее, чем повторить настроенное вторым разом."""
        self._write_cfg()
        with mock.patch.object(llm, "_usage_load", return_value={}):
            line = llm.state_line()
        self.assertIn("сегодня не наблюдался", line)

    def test_configured_via_test_seam(self):
        llm.use_test_client(None)
        self.assertFalse(llm.configured())
        llm.use_test_client(object())
        self.assertTrue(llm.configured())


def _chunk(content=None, tool_calls=None, finish=None, usage=None):
    delta = types.SimpleNamespace(content=content, tool_calls=tool_calls or [])
    choice = types.SimpleNamespace(delta=delta, finish_reason=finish)
    return types.SimpleNamespace(choices=[choice], usage=usage)


class FakeStreamOpenAI:
    """openai-клиент, чей create(stream=True) отдаёт ИТЕРАТОР чанков (как relay/реальный stream)."""
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kw):
        self.calls.append(kw)
        return iter(self.chunks)


class TestOpenAIStreaming(Base):
    def test_stream_text_and_usage_aggregated(self):
        self._write_cfg(voice={"framework": "openai", "model": "gpt-x"})
        chunks = [_chunk(content="pi"), _chunk(content="ng"),
                  _chunk(finish="stop",
                         usage=types.SimpleNamespace(prompt_tokens=5, completion_tokens=1))]
        fo = FakeStreamOpenAI(chunks)
        llm.use_test_client(fo, "openai")
        resp = llm.chat("voice", messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(resp.text, "ping")
        self.assertEqual(resp.stop_reason, "end_turn")
        self.assertEqual(resp.usage, {"in": 5, "out": 1})
        self.assertTrue(fo.calls[0].get("stream"), "openai-путь должен просить stream")

    def test_stream_tool_calls_aggregated(self):
        self._write_cfg(voice={"framework": "openai", "model": "gpt-x"})
        tc1 = types.SimpleNamespace(index=0, id="c1",
                                    function=types.SimpleNamespace(name="recall", arguments='{"q":'))
        tc2 = types.SimpleNamespace(index=0, id=None,
                                    function=types.SimpleNamespace(name=None, arguments='"x"}'))
        chunks = [_chunk(tool_calls=[tc1]), _chunk(tool_calls=[tc2]), _chunk(finish="tool_calls")]
        llm.use_test_client(FakeStreamOpenAI(chunks), "openai")
        resp = llm.chat("voice", messages=[{"role": "user", "content": "hi"}],
                        tools=[{"name": "recall", "description": "", "input_schema": {"type": "object"}}])
        self.assertEqual(resp.stop_reason, "tool_use")
        tu = [b for b in resp.blocks if b["type"] == "tool_use"][0]
        self.assertEqual(tu["name"], "recall")
        self.assertEqual(tu["input"], {"q": "x"})


class TestModelRotation(Base):
    def test_models_url_variants(self):
        self.assertEqual(llm._models_url("http://host.docker.internal:5011"),
                         "http://host.docker.internal:5011/v1/models")
        self.assertEqual(llm._models_url("https://api.z.ai/api/anthropic"),
                         "https://api.z.ai/api/anthropic/v1/models")
        self.assertEqual(llm._models_url("https://api.openai.com/v1"),
                         "https://api.openai.com/v1/models")
        self.assertEqual(llm._models_url("http://x/v1/"), "http://x/v1/models")

    def test_pick_replacement_family_and_highest(self):
        avail = ["glm-4.5", "glm-4.6", "glm-4.7", "glm-5", "glm-5.1", "glm-5.2", "gpt-5.4"]
        self.assertEqual(llm._pick_replacement("glm-5.3", avail), "glm-5.2")
        self.assertEqual(llm._pick_replacement("gpt-9", avail), "gpt-5.4")

    def test_resolve_keeps_present_rotates_missing_noop_empty(self):
        self._orig.append((llm, "_available_models", llm._available_models))
        llm._available_models = lambda fw: ["glm-5.1", "glm-5.2"]
        self.assertEqual(llm._resolve_model("anthropic", "glm-5.2"), "glm-5.2")   # живо
        self.assertEqual(llm._resolve_model("anthropic", "glm-5.9"), "glm-5.2")   # пропало -> старшая
        llm._available_models = lambda fw: []
        self.assertEqual(llm._resolve_model("anthropic", "glm-5.9"), "glm-5.9")   # пусто -> не трогаем

    def test_generic_gpt_56_compatibility_alias_rotates_to_sol(self):
        self._orig.append((llm, "_available_models", llm._available_models))
        llm._available_models = lambda fw: [
            "gpt-5.6",  # stale discovery may advertise it although calls return 400
            "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.5"]
        self.assertEqual(llm._resolve_model("openai", "gpt-5.6"), "gpt-5.6-sol")

    def test_available_models_never_touches_the_wire(self):
        """⚠ Прежний ассерт смотрел на РЕЗУЛЬТАТ, а не на транспорт.

        `[]` — это одновременно ответ стража и ответ ветки отказа (`except: ids = []`).
        Убери страж — тест остался бы зелёным в ЛЮБОМ окружении: запрос ушёл бы с
        пустым бирером, вернулся бы 401, и пустой список пришёл бы «правильным».
        Ровно через этот шов сеть и текла: `panel.brain_catalog()` делал два живых
        HTTPS-запроса на каждый свой тест. Теперь смотрим на провод.
        """
        llm._MODELS_CACHE.clear()
        self.addCleanup(llm._MODELS_CACHE.clear)
        llm.use_test_client(FakeStreamOpenAI([]), "openai")
        with mock.patch("urllib.request.urlopen") as opened:
            self.assertEqual(llm._available_models("openai"), [])
        self.assertFalse(opened.called, "фейк-клиент — сеть не трогаем")

    def test_available_models_is_muted_even_without_a_registered_fake(self):
        """Тот путь, которым текло: у `panel.brain_catalog` фейка нет вовсе."""
        llm._MODELS_CACHE.clear()
        self.addCleanup(llm._MODELS_CACHE.clear)
        with mock.patch.dict(llm._TEST_CLIENTS, {}, clear=True), \
             mock.patch("urllib.request.urlopen") as opened:
            self.assertEqual(llm._available_models("anthropic"), [])
        self.assertFalse(opened.called, "под стендом каталог моделей не ходит наружу")



class TestGlmEffortDialect(Base):
    """glm-*: ступень роли обязана доезжать до запроса z.ai-диалектом.

    Живой замер 24.08 после переключения голоса на glm-5.3: обычные вызовы шли
    БЕЗ thinking-поля, глубину выбирал сервер (дефолт max) — 10-15с на ответ.
    Ступень роли на anthropic-пути «принималась-и-игнорировалась», и владелец
    не мог сделать reasoning low ни конфигом, ни рукой. Проекция словаря реле
    в словарь glm (low/high/max) обязана: ехать в запрос thinking+extra_body,
    уступать явному thinking-бюджету, не трогать не-glm модели и работать на
    фолбэк-плече.
    """

    def _glm_cfg(self, effort="low"):
        cfg = llm._from_env()
        cfg["frameworks"]["anthropic"]["api_key"] = "zai-key"
        cfg["roles"]["voice"].update({"framework": "anthropic", "model": "glm-5.3",
                                       "reasoning_effort": effort})
        llm.save_config(cfg)
        return llm._config()

    def test_role_effort_projects_into_request(self):
        self._glm_cfg("low")
        fake = FakeAnthropic([FakeAnthResp("ок")])
        llm.use_test_client(fake)
        llm.chat("voice", messages=[{"role": "user", "content": "привет"}])
        kw = fake.calls[0]
        self.assertEqual(kw.get("thinking"), {"type": "enabled"},
                         "без thinking z.ai думает на серверном max")
        self.assertEqual(kw.get("extra_body"), {"output_config": {"effort": "low"}})

    def test_relay_steps_project_to_glm_dialect(self):
        for step, glm_step in (("xhigh", "max"), ("high", "high"),
                               ("minimal", "low")):
            self._glm_cfg(step)
            fake = FakeAnthropic([FakeAnthResp("ок")])
            llm.use_test_client(fake)
            llm.chat("voice", messages=[{"role": "user", "content": "привет"}])
            self.assertEqual(
                fake.calls[0].get("extra_body"), {"output_config": {"effort": glm_step}},
                f"ступень реле {step!r} обязана проецироваться в {glm_step!r}")

    def test_explicit_budget_beats_effort_step(self):
        self._glm_cfg("low")
        fake = FakeAnthropic([FakeAnthResp("ок")])
        llm.use_test_client(fake)
        llm.chat("voice", messages=[{"role": "user", "content": "подумай"}],
                 thinking=2048)
        kw = fake.calls[0]
        self.assertEqual(kw.get("thinking"),
                         {"type": "enabled", "budget_tokens": 2048})
        self.assertEqual(kw.get("extra_body"), {"output_config": {"effort": "low"}})

    def test_non_glm_model_keeps_effort_ignored(self):
        cfg = llm._from_env()
        cfg["frameworks"]["anthropic"]["api_key"] = "zai-key"
        cfg["roles"]["voice"].update({"framework": "anthropic",
                                       "model": "claude-4-sonnet",
                                       "reasoning_effort": "low"})
        llm.save_config(cfg)
        fake = FakeAnthropic([FakeAnthResp("ок")])
        llm.use_test_client(fake)
        llm.chat("voice", messages=[{"role": "user", "content": "привет"}])
        kw = fake.calls[0]
        self.assertNotIn("thinking", kw, "не-glm модели — байт-в-байт как раньше")
        self.assertNotIn("extra_body", kw)

    def test_fallback_leg_to_glm_carries_effort_step(self):
        cfg = llm._from_env()
        cfg["frameworks"]["anthropic"]["api_key"] = "zai-key"
        cfg["frameworks"]["openai"]["api_key"] = "sk-test"
        cfg["roles"]["voice"].update({"framework": "openai", "model": "gpt-x",
                                       "reasoning_effort": "low",
                                       "fallback_framework": "anthropic",
                                       "fallback_model": "glm-5.3"})
        llm.save_config(cfg)
        broken = FakeOpenAI()

        def _boom(**kw):
            raise RateLimitError("429 dead")

        broken.create = _boom
        llm.use_test_client(broken, "openai")
        fake = FakeAnthropic([FakeAnthResp("ок")])
        llm.use_test_client(fake)
        resp = llm.chat("voice", messages=[{"role": "user", "content": "привет"}])
        self.assertEqual(resp.framework, "anthropic")
        kw = fake.calls[0]
        self.assertEqual(kw.get("thinking"), {"type": "enabled"},
                         "glm-5.3 как фолбэк мёртв без thinking на фолбэк-плече")
        self.assertEqual(kw.get("extra_body"), {"output_config": {"effort": "low"}})


class TheCallTraceJournalIsBounded(unittest.TestCase):
    """`_CALL_TRACE_MAX = 20000` был мёртвой константой: её никто не читал, журнал
    рос без потолка (21 368 строк на 28.08 при диске 86%). Ротация — по размеру,
    атомарным rename в `.1`: параллельный дописывающий уезжает вместе со старым
    файлом, и на шве не теряется ни строки."""

    def test_rotation_is_atomic_and_loses_no_seam_rows(self):
        tmp = Path(tempfile.mkdtemp(prefix="trace_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with mock.patch.object(llm, "_CALL_TRACE", tmp / "llm_calls.jsonl"), \
             mock.patch.object(llm, "_CALL_TRACE_ROTATE_BYTES", 700):
            for i in range(30):
                llm._call_trace(f"r{i:02d}", "m", ok=True, cached=0, prompt=1,
                                out_tokens=1, latency_ms=1.0)
        main = tmp / "llm_calls.jsonl"
        rolled = tmp / "llm_calls.jsonl.1"
        self.assertTrue(rolled.exists(), "ротация не случилась")
        self.assertLess(main.stat().st_size, 1400, "потолок не ограничивает файл")
        rows = [json.loads(line) for line in
                (rolled.read_text(encoding="utf-8")
                 + main.read_text(encoding="utf-8")).splitlines()]
        indices = [int(row["role"][1:]) for row in rows]
        self.assertEqual(indices, list(range(indices[0], 30)),
                         "на шве ротации потерялись строки")


if __name__ == "__main__":
    unittest.main(verbosity=2)
