"""Deliver canonical Forge completion events in Hélène's single runner loop."""
from __future__ import annotations

import json
import logging
import os
import time

log = logging.getLogger("helene.forge-events")


def key(event):
    return str(event.get("dedup_key") or event.get("id") or "")


class ForgeEvents:
    def __init__(self, continuity, events, forge, perception):
        self.continuity, self.events = continuity, events
        self.forge, self.perception = forge, perception
        self.path = continuity.desks.tree / "memory" / ".state" / "helene-forge-handoff.json"
        self.last = 0.0

    def _save(self, claim):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as sink:
            json.dump(claim, sink, ensure_ascii=False)
            sink.flush()
            os.fsync(sink.fileno())
        temp.replace(self.path)

    def _accepted(self, events):
        self.events.mark_delivered([key(event) for event in events])
        bridge = []
        for event in events:
            payload = event.get("payload") or {}
            unit = f"{payload.get('task_id')}:{payload.get('agent_id')}"
            if key(event) == "forge:" + unit:
                bridge.append(unit)
            elif key(event).endswith(":overdue"):
                bridge.append(unit + ":overdue")
        self.forge.mark_seen(bridge)

    def _reconcile(self):
        if not self.path.exists():
            return False
        claim = json.loads(self.path.read_text(encoding="utf-8"))
        if not claim:
            return False
        if claim.get("owner_stamp") != self.continuity.owner_stamp():
            raise ValueError("Forge event handoff belongs to a different local owner")
        run_id = claim["run_id"]
        manager = self.continuity.agent._runs()
        if run_id not in manager.run_ids():
            if list(manager.root.glob("*/" + run_id)):
                raise ValueError("partial Forge event run needs inspection; handoff retained")
            self._save({})
            return False
        context = manager.context(run_id)
        channel, snapshot = self.continuity.agent._load_exact_run_channel(manager, context)
        if context.kind != "task_window" or not channel.praxis_self:
            raise ValueError("Forge handoff points to a different run authority")
        status = manager.manifest(run_id)["status"]
        if self.continuity.closed_without_effects(run_id):
            self._save({})
            return False  # Retry the event under the core's existing attempt cap.
        if status in {"done", "cancelled"}:
            # A completed run is the acceptance receipt; a human cancellation
            # must not resurrect the same work as another event activation.
            self._accepted(claim["events"])
            self._save({})
            return False
        if status == "failed":
            self._save({})
            return False
        return True  # Resume/control owns this run. Never create a second one.

    def tick(self):
        if not self.events.enabled() or not self.continuity.agent.llm.configured():
            return
        self.forge.reconcile_subagent_events()
        if self._reconcile():
            return
        gap = float(self.perception.value("forge_event_gap_sec"))
        if time.time() - self.last < gap:
            return
        pending = self.events.undelivered({"subagent_result"})
        if not pending:
            return
        self.last = time.time()
        counts = self.events.bump_attempts([key(event) for event in pending])
        shown = self.forge._wake_load_seen()
        quiet, loud, exhausted = [], [], []
        for event in pending:
            payload = event.get("payload") or {}
            if counts.get(key(event), 1) > self.events.MAX_DELIVERY_ATTEMPTS:
                exhausted.append(event)
            elif ((payload.get("causality") or {}).get("cancelled_by") == "praxis"
                  or payload.get("reported_inline")
                  or f"{payload.get('task_id')}:{payload.get('agent_id')}" in shown):
                quiet.append(event)
            else:
                loud.append(event)
        if exhausted:
            log.error("Forge: исчерпаны попытки ядра (%s); события: %s",
                      self.events.MAX_DELIVERY_ATTEMPTS, [key(e) for e in exhausted])
        self._accepted(quiet + exhausted)
        if loud:
            def before(context):
                self._save({"schema": "helene.forge-handoff.v1", "run_id": context.run_id,
                            "owner_stamp": self.continuity.owner_stamp(), "events": loud})
            try:
                with self.continuity.activation(before):
                    self.continuity.agent.forge_event_turn(loud)
            finally:
                self._reconcile()
        self.events.compact()
