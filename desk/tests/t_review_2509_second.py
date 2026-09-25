# -*- coding: utf-8 -*-
"""Стенды второй фазы ревью 25.09 (адверсарии V1–V4) — движок и сборка.

* V3 F3 — расписка мозга от 0.8.5–0.8.7 без `projected` дописывается при неизменном
  helene.json, и убранный позже запасной снимается из llm.json.
* V1-7 — `extensions.scrub` маскирует всё поддерево под секретным ключом и userinfo в адресах.
* V3 F4 — репетиция расширений даёт `tree()` и `config()`, как живая загрузка.
* V1-5 — паспорт поставки не несёт абсолютных путей машины сборки.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "localharness"))
sys.path.insert(0, str(ROOT / "installer"))

import boot  # noqa: E402
import extensions  # noqa: E402


class BrainReceiptUpgrade(unittest.TestCase):
    def test_receipt_without_projected_is_completed_and_fallback_is_later_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            (tree / "memory").mkdir(parents=True)
            cfg_path = Path(tmp) / "helene.json"
            with_fallback = {
                "model": {"framework": "openai", "name": "gpt-x", "key": "sk-main", "base_url": "http://a",
                          "fallback_framework": "anthropic", "fallback_name": "claude-x",
                          "fallback_key": "zai-SECRET", "fallback_base_url": "http://b"},
            }
            cfg_path.write_text(json.dumps(with_fallback), encoding="utf-8")
            # 0.8.7: проекция была, расписка без `projected`
            first = boot.project_brain(tree, cfg_path) if hasattr(boot, "project_brain") else None
            receipt = tree / "memory" / "brain_projection.json"
            if not receipt.exists():
                candidates = list((tree / "memory").glob("*projection*"))
                self.assertTrue(candidates, "расписка проекции не найдена")
                receipt = candidates[0]
            loaded = json.loads(receipt.read_text(encoding="utf-8"))
            loaded.pop("projected", None)
            receipt.write_text(json.dumps(loaded), encoding="utf-8")
            llm_path = tree / "memory" / "llm.json"
            self.assertIn("zai-SECRET", llm_path.read_text(encoding="utf-8"))
            # 0.8.8+: helene.json не менялся — расписка дописывается
            boot.project_brain(tree, cfg_path)
            self.assertIn("projected", json.loads(receipt.read_text(encoding="utf-8")))
            # владелец убрал запасного
            without = {"model": {"framework": "openai", "name": "gpt-x", "key": "sk-main", "base_url": "http://a"}}
            cfg_path.write_text(json.dumps(without), encoding="utf-8")
            boot.project_brain(tree, cfg_path)
            self.assertNotIn("zai-SECRET", llm_path.read_text(encoding="utf-8"),
                             "убранный ключ запасного жил бы в llm.json вечно (V3 F3)")


class ScrubMasksSubtreesAndUserinfo(unittest.TestCase):
    def test_lists_dicts_numbers_and_new_key_names_are_masked(self):
        cfg = {"env": {"HTTPS_PROXY": "http://user:p4ss@proxy:3128", "DATABASE_URL": "postgres://u:p@h/db",
                       "IMAP_PASS": "x", "GH_PAT": "ghp_1", "PLAIN": "ok"},
               "headers": {"Authorization": "Bearer abc"},
               "keys": ["k1", "k2"], "secret": {"value": "v"}, "token": 12345,
               "telegram": {"api_id": 123, "api_hash": "h", "owner_id": 5}}
        out = extensions.scrub(cfg)
        self.assertEqual(out["env"]["HTTPS_PROXY"], "•••")
        self.assertEqual(out["env"]["DATABASE_URL"], "•••")
        self.assertEqual(out["env"]["IMAP_PASS"], "•••")
        self.assertEqual(out["env"]["GH_PAT"], "•••")
        self.assertEqual(out["env"]["PLAIN"], "ok")
        self.assertEqual(out["headers"]["Authorization"], "•••")
        self.assertEqual(out["keys"], "•••")
        self.assertEqual(out["secret"], "•••")
        self.assertEqual(out["token"], "•••")
        self.assertEqual(out["telegram"]["api_hash"], "•••")
        self.assertEqual(out["telegram"]["owner_id"], 5)

    def test_userinfo_in_any_string_is_cut(self):
        self.assertEqual(extensions.scrub({"note": "see http://bob:hunter2@host/x"})["note"],
                         "see http://•••@host/x")
        self.assertEqual(extensions.scrub({"note": "http://host/x"})["note"], "http://host/x")


class RehearsalSeesTreeAndConfig(unittest.TestCase):
    def test_check_passes_data_dir_and_config_to_the_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            ext = data / "extensions" / "probe"
            ext.mkdir(parents=True)
            (Path(tmp) / "helene.json").write_text(json.dumps({"telegram": {"owner_id": 7, "api_hash": "h"}}),
                                                    encoding="utf-8")
            (ext / "extension.json").write_text(json.dumps({
                "name": "probe", "version": "1.0", "api": f"{extensions.API_NAME}/{extensions.API_VERSION}",
                "entry": "ext:register"}), encoding="utf-8")
            (ext / "ext.py").write_text(
                "def register(api):\n"
                "    assert api.tree() is not None, 'tree() пуст на репетиции'\n"
                "    assert api.config()['telegram']['owner_id'] == 7\n"
                "    assert api.config()['telegram']['api_hash'] == '•••'\n",
                encoding="utf-8")
            report = extensions.check(data, host_version="0.9.0")
            self.assertTrue(report["ok"], report)


class PassportPathsAreRelative(unittest.TestCase):
    def test_core_provenance_paths_do_not_name_the_build_machine(self):
        import build_dist
        note = build_dist.core_provenance(ROOT.parent / "port-2409") if (ROOT.parent / "port-2409").is_dir() \
            else build_dist.core_provenance(ROOT.parent / "live")
        if not note:
            self.skipTest("ядро и слой не прочитались")
        for part in ("core", "layer"):
            path = str(note[part].get("path") or "")
            self.assertNotIn("Users", path, path)
            self.assertFalse(Path(path).is_absolute(), path)


if __name__ == "__main__":
    unittest.main()
