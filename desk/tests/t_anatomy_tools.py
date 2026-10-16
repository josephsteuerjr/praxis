# -*- coding: utf-8 -*-
"""Producer -> reader contract for the owner-window tool catalog.

Run: python desk/tests/t_anatomy_tools.py (from repository root).
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / "localharness")]
import runner  # noqa: E402
from deskd import readers  # noqa: E402


class AnatomyTools(unittest.TestCase):
    def test_effective_catalog_and_direct_pointer_manifest_reach_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            called = []
            direct = {"name": "fs_read", "description": "Read", "input_schema": {"properties": {"path": {}}, "required": ["path"]}}
            indirect = {"name": "coding_agent", "description": "Delegate after Hélène overlay", "input_schema": {"properties": {"action": {}}}}
            via = {"name": "call", "description": "Invoke by name", "input_schema": {"properties": {"name": {}}}}
            class Agent:
                ChannelContext = types.SimpleNamespace
                @staticmethod
                def catalog_tools_for(ctx):
                    called.append(("catalog", ctx.owner, ctx.chat_id))
                    return [direct, indirect]
                @staticmethod
                def offered_tools_for(ctx):
                    called.append(("offered", ctx.owner, ctx.chat_id))
                    return [direct, via]
            cfg = {"model": {"model": "test", "key": "PRIVATE_123456789"}}
            with mock.patch.object(runner, "_agent", Agent), \
                 mock.patch.object(runner, "_git_state", return_value={}), \
                 mock.patch.object(runner, "_sandbox_state", return_value={}), \
                 mock.patch.object(runner, "_computer_state", return_value={}), \
                 mock.patch.object(runner, "_brain_ready", return_value=False), \
                 mock.patch.object(runner.broker, "state", return_value={}), \
                 mock.patch.object(runner, "_shut_anatomy"), \
                 mock.patch.object(readers, "tree", return_value=tree):
                runner._write_anatomy(tree, cfg)
                observed = readers.anatomy()
            rows = {r["name"]: r for r in observed["tools"]}
            self.assertEqual([x[0] for x in called], ["offered", "catalog"])
            self.assertEqual({x[1:] for x in called}, {(True, "window")})
            self.assertEqual(set(rows), {"fs_read", "coding_agent", "call"})
            self.assertEqual(rows["fs_read"]["invocation"], "direct")
            self.assertEqual(rows["coding_agent"]["invocation"], "via_call")
            self.assertEqual(rows["call"]["invocation"], "direct")
            self.assertEqual(rows["coding_agent"]["desc"], indirect["description"])
            self.assertEqual(rows["fs_read"]["required"], ["path"])
            self.assertNotIn(cfg["model"]["key"], json.dumps(observed))


if __name__ == "__main__":
    unittest.main()
