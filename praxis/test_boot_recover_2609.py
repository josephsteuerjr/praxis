"""26.09 — бут не разбирает под замком каждый из тысяч решённых прогонов.

Профиль бута 26.09 (py-spy с хоста): `recover_durable_state` → `RunManager.recover` и
пост-восстановление брали замок и полностью разбирали манифест у всех ~8,5 тыс. прогонов —
все терминальные (8016 done, 328 failed, 189 cancelled, итог не продвинут у одного). «На
связи» ждало этого десяток минут. Сырое чтение всех манифестов — 2,7 с.

Прибито: решённый прогон (терминальный, манифест покрывает WAL — как у `live_run_ids`) с
записанным итогом и RECAP.md пропускается без замка; идущий прогон по-прежнему встаёт на
паузу; терминальный без итога или с незаконченным продвижением итога — прежним путём.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from run_context import RunContext
from run_manager import RunManager


class RecoverSkipsSettledRuns(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.manager = RunManager(Path(self._tmp.name))

    def create(self, suffix: str) -> str:
        ctx = RunContext.create(run_id=f"run-{suffix}", kind="computer", goal=f"g {suffix}",
                                principal_id="telegram:1", scope="owner", origin_chat_id="1")
        self.manager.create(ctx, "# t\n")
        return ctx.run_id

    def done_with_recap(self, suffix: str) -> str:
        run_id = self.create(suffix)
        self.manager.transition(run_id, "running")
        self.manager.transition(run_id, "done")
        self.manager.write_recap(run_id, f"# итог {suffix}", promote=False)
        return run_id

    def test_settled_runs_are_not_locked_running_ones_are_paused(self):
        settled = [self.done_with_recap(f"s{i}") for i in range(5)]
        running = self.create("live")
        self.manager.transition(running, "running")
        no_recap = self.create("norecap")
        self.manager.transition(no_recap, "running")
        self.manager.transition(no_recap, "done")
        fresh = RunManager(Path(self._tmp.name))      # как после рестарта: кэшей нет
        locked: list[str] = []
        real = fresh._locked

        def spy(run_dir):
            locked.append(Path(run_dir).name)
            return real(run_dir)
        with mock.patch.object(fresh, "_locked", spy):
            reports = fresh.recover()
        self.assertEqual(fresh.manifest(running)["status"], "paused")
        self.assertTrue(any(r.get("run_id") == running for r in reports))
        for run_id in settled:
            self.assertNotIn(run_id, locked, "решённый прогон с итогом — без замка")
        self.assertIn(running, locked)
        self.assertIn(no_recap, locked, "терминальный без итога — прежним путём")

    def test_pending_recap_promotion_is_still_recovered(self):
        run_id = self.done_with_recap("promo")
        fresh = RunManager(Path(self._tmp.name))
        live = set(fresh.live_run_ids())
        self.assertTrue(fresh.settled_without_work(run_id, live=live))
        manifest = fresh.manifest(run_id)
        manifest["recap"] = dict(manifest["recap"], promotion={"status": "running"})
        (fresh.path(run_id) / "manifest.json").write_text(
            __import__("json").dumps(manifest), encoding="utf-8")
        self.assertFalse(fresh.settled_without_work(run_id, live=live),
                         "висящее продвижение итога — прежним путём")

    def test_unknown_liveness_falls_back_to_the_locked_path(self):
        run_id = self.done_with_recap("x")
        self.assertFalse(self.manager.settled_without_work(run_id, live=None))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
