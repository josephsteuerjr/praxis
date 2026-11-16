"""Windows-MCP's control consent prompt works in the embeddable runtime.

Upstream shows the prompt in a Tk child process; Hélène's component Python has no tkinter,
so the child exited 1, the answer was "error" and every first Move/Click/Type failed
CONTROL_UNAVAILABLE with status "ready" (owner's agent, 16.11.2026). The entry now replaces
only the prompt window with a native message box under the same allow/deny/timeout/error
contract.
"""
from pathlib import Path
import os
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK))
from localharness import ui_language, windows_mcp_consent as consent


class Box:
    def __init__(self, result=6, error=None):
        self.result, self.error, self.calls = result, error, []

    def __call__(self, *args):
        self.calls.append(args)
        if self.error:
            raise self.error
        return self.result


class NativePrompt(unittest.TestCase):
    def test_answers_keep_the_upstream_contract(self):
        for code, answer in ((6, "allow"), (7, "deny"), (32000, "timeout"), (0, "error"), (2, "error")):
            with self.subTest(code=code):
                self.assertEqual(consent.ask(10, box=Box(code), environ={}), answer)

    def test_a_failing_box_never_grants(self):
        self.assertEqual(consent.ask(10, box=Box(error=OSError("no desktop")), environ={}), "error")
        self.assertEqual(consent.ask("x", box=Box(6), environ={}), "error")

    def test_prompt_text_timeout_and_safe_default(self):
        box = Box(32000)
        consent.ask(7, box=box, environ={"HELENE_WINDOWS_MCP_CONSENT_TEXT": "через {seconds} с",
                                         "HELENE_WINDOWS_MCP_CONSENT_TITLE": "T"})
        owner, text, title, flags, language, milliseconds = box.calls[0]
        self.assertEqual((owner, text, title, language, milliseconds), (None, "через 7 с", "T", 0, 7000))
        self.assertTrue(flags & 0x4 and flags & 0x100 and flags & 0x40000)  # yes/no, "No" default, topmost
        consent.ask(10 ** 6, box=box, environ={})
        self.assertEqual(box.calls[-1][5], 600000)
        self.assertIn("10", consent.TEXT.replace("{seconds}", "10"))

    def test_entry_replaces_the_tk_prompt_before_the_server_starts(self):
        upstream = types.ModuleType("control_consent")
        upstream.ask = lambda seconds: "error"  # what the Tk child gave without tkinter
        stubs = {name: types.ModuleType(name) for name in (
            "windows_mcp", "windows_mcp.infrastructure", "windows_mcp.infrastructure.config",
            "windows_mcp.infrastructure.analytics", "windows_mcp.desktop", "windows_mcp.__main__")}
        stubs["windows_mcp.infrastructure"].config = stubs["windows_mcp.infrastructure.config"]
        stubs["windows_mcp.infrastructure"].analytics = stubs["windows_mcp.infrastructure.analytics"]
        stubs["windows_mcp.infrastructure.analytics"].PostHogAnalytics = type("P", (), {})
        stubs["windows_mcp.desktop"].control_consent = upstream
        stubs["windows_mcp.desktop.control_consent"] = upstream
        stubs["windows_mcp.__main__"].main = lambda: None
        stubs["windows_mcp"].infrastructure = stubs["windows_mcp.infrastructure"]
        stubs["windows_mcp"].desktop = stubs["windows_mcp.desktop"]
        with tempfile.TemporaryDirectory() as state, patch.dict(sys.modules, stubs), \
                patch.dict(os.environ, {"HELENE_WINDOWS_MCP_STATE": state}):
            runpy.run_path(str(DESK / "localharness" / "windows_mcp_entry.py"), run_name="helene_entry")
            self.assertEqual(upstream.ask.__name__, "ask")
            self.assertEqual(upstream.ask.__code__.co_filename, str(DESK / "localharness" / "windows_mcp_consent.py"))

    def test_runtime_text_exists_in_every_interface_language(self):
        for language in ("ru", "en", "fr", "zh"):
            text = ui_language.text("native.mcp.consent_text", language, {"seconds": "{seconds}"})
            self.assertIn("{seconds}", text, language)
        source = (DESK / "localharness" / "windows_mcp_runtime.py").read_text("utf-8")
        self.assertIn("HELENE_WINDOWS_MCP_CONSENT_TEXT=words.text(self.owner.root,'consent_text',seconds='{seconds}')", source)

    @unittest.skipUnless(sys.platform == "win32" and os.environ.get("HELENE_NATIVE_CONSENT_PROBE") == "1",
                         "shows a real 1-second prompt on the desktop")
    def test_real_message_box_times_out_without_granting_by_click(self):
        self.assertEqual(consent.ask(1, environ={}), "timeout")


if __name__ == "__main__":
    unittest.main()
