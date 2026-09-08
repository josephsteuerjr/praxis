"""Разрезы кэша по группам действий для экрана «Система»: взвешенная доля, оси, чтение без записи."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deskd import frame_cuts, readers


class FrameCuts(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(os.environ, {"HELENE_TREE": str(self.root)}); env.start(); self.addCleanup(env.stop)

    def _run(self, when: datetime, kind: str, calls: list[dict]):
        rid = f"run-{when:%Y%m%dT%H%M%S}000000Z-{abs(hash(when)) % 0xFFFFFFFF:08x}"
        path = self.root / "memory/runs" / f"{when:%Y-%m}" / rid
        path.mkdir(parents=True)
        (path / "manifest.json").write_text(json.dumps({"context": {"kind": kind, "model_profile": "voice"}}), encoding="utf-8")
        rows = []
        seq = 0
        for i, c in enumerate(calls):
            seq += 1; rows.append({"kind": "model_input", "call_id": f"m{i}", "seq": seq, "metadata": {"mode": "on" if i == 0 else "", "reused": i > 0}})
            seq += 1; rows.append({"kind": "model_completed", "call_id": f"m{i}", "seq": seq, "run_id": rid, "role": "voice", "model": "glm-5.3",
                                   "duration_ms": c["ms"], "stop_reason": c.get("stop", "tool_use"), "text_chars": c.get("text", 0),
                                   "usage": {"in": c["in"], "cache_read": c["cached"], "out": c["out"]}, "at": when.isoformat()})
            if c.get("tool"):
                seq += 1; rows.append({"kind": "tool_started", "call_id": f"c{i}", "tool": c["tool"], "seq": seq})
        (path / "events.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        return rid

    def test_weighted_ratio_axes_and_window(self):
        now = datetime.now(timezone.utc)
        self._run(now - timedelta(hours=1), "chat_turn", [
            {"in": 30000, "cached": 10000, "out": 300, "ms": 12000, "tool": "computer"},   # первая: 25%
            {"in": 500, "cached": 39500, "out": 50, "ms": 3000, "tool": "reply"},          # продолжение: 98.75%
            {"in": 100, "cached": 39900, "out": 0, "ms": 100000, "stop": "max_tokens"},    # обрыв, без руки и слова
        ])
        self._run(now - timedelta(days=20), "chat_turn", [{"in": 1, "cached": 0, "out": 1, "ms": 1, "tool": "shell"}])  # старый — вне окна
        out = readers.frame_cuts(7)
        s = out["summary"]
        self.assertEqual((s["calls"], s["runs"]), (3, 1), "прогон старше окна не считается")
        self.assertAlmostEqual(s["cache_ratio"], (10000 + 39500 + 39900) / (30600 + 89400), places=3)
        by_iter = {r["group"]: r for r in out["by"]["iteration"]}
        self.assertAlmostEqual(by_iter["первая"]["cache_ratio"], 0.25, places=3)
        self.assertAlmostEqual(by_iter["продолжение"]["cache_ratio"], 79400 / 80000, places=3)
        hands = {r["group"]: r for r in out["by"]["hand"]}
        self.assertEqual(set(hands), {"computer", "reply", "обрыв"})
        self.assertEqual(hands["обрыв"]["cuts"], 1)
        self.assertEqual(hands["computer"]["median_ms"], 12000)
        self.assertEqual(out["by"]["kind"][0]["group"], "chat_turn")
        # Ничего не записано в дерево
        self.assertEqual(sorted(p.name for p in (self.root / "memory").iterdir()), ["runs"])

    def test_empty_tree_is_an_honest_zero(self):
        out = readers.frame_cuts(7)
        self.assertEqual(out["summary"]["calls"], 0)
        self.assertIsNone(out["summary"]["cache_ratio"])
        self.assertEqual(frame_cuts.group([], "hand"), [])


if __name__ == "__main__":
    unittest.main()
