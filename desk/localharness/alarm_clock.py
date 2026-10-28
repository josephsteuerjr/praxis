"""Local alarm provenance and claim-to-run handoff. tasks.json remains canonical."""
from __future__ import annotations

from contextvars import ContextVar
import functools
import logging

import transport

log = logging.getLogger("helene.alarms")


class AlarmClock:
    def __init__(self, continuity, tasks):
        self.continuity, self.tasks = continuity, tasks
        self._adding = ContextVar("helene_alarm_origin", default=None)

    def install(self):
        original_add, original_save = self.tasks.add, self.tasks._save

        @functools.wraps(original_add)
        def add(*args, **kwargs):
            channel = self.continuity.agent._TURN_CHANNEL.get()
            room = str(getattr(channel, "chat_id", "") or "")
            if not room and getattr(channel, "praxis_self", False):
                room = transport.ROOM_DEFAULT
            origin = None
            if transport.is_room(room) and (getattr(channel, "owner", False) or getattr(channel, "praxis_self", False)):
                origin = {"room": room, "owner_stamp": self.continuity.owner_stamp()}
            token = self._adding.set((origin, {t.get("id") for t in self.tasks._load()}))
            try:
                return original_add(*args, **kwargs)
            finally:
                self._adding.reset(token)

        @functools.wraps(original_save)
        def save(items):
            adding = self._adding.get()
            if adding is not None and adding[0] is not None:
                for item in items:
                    if item.get("id") not in adding[1]:
                        # Same atomic tasks.json replacement as the intent itself.
                        item["helene_origin"] = dict(adding[0])
            return original_save(items)

        self.tasks.add, self.tasks._save = add, save

    def room(self, task):
        origin = task.get("helene_origin")
        if origin is None:
            log.warning("будильник #%s без старой привязки: прежняя комната window", task["id"])
            return transport.ROOM_DEFAULT
        room = str(origin.get("room") or "")
        if not transport.is_room(room) or origin.get("owner_stamp") != self.continuity.owner_stamp():
            raise ValueError("alarm origin is invalid or its local owner changed")
        return room

    def _change_claim(self, task_id, callback):
        items = self.tasks._load()
        for item in items:
            if item.get("id") == task_id and item.get("status") == "pending" and item.get("claim"):
                callback(item)
                self.tasks._save(items)
                return True
        return False

    def reconcile_claims(self):
        manager = self.continuity.agent._runs()
        for task in self.tasks.open_claims():
            claim = task.get("claim") or {}
            run_id = str(claim.get("helene_run_id") or "")
            if not run_id:
                self._change_claim(task["id"], lambda item: item.pop("claim", None))
                continue
            try:
                # No manifest and no partial directory means creation never began.
                if run_id not in manager.run_ids():
                    if list(manager.root.glob("*/" + run_id)):
                        raise ValueError("incomplete durable run needs inspection; claim retained")
                    self._change_claim(task["id"], lambda item: item.pop("claim", None))
                    continue
                context = manager.context(run_id)
                self.continuity.verify_local_owner(run_id, context)
                if (context.delivery_chat_id != claim.get("helene_room")
                        or claim.get("helene_owner_stamp") != self.continuity.owner_stamp()):
                    raise ValueError("alarm claim differs from durable run authority")
                if self.continuity.closed_without_effects(run_id):
                    self._change_claim(task["id"], lambda item: item.pop("claim", None))
                    continue
                # Creation alone is not acceptance: a crash before the first
                # model output leaves an evidence-free orphan. Keep the claim
                # while resume/control owns the run; terminal state is a receipt.
                if manager.manifest(run_id)["status"] not in {"done", "failed", "cancelled"}:
                    continue
                self.tasks.mark_fired(task["id"])
                log.info("будильник #%s передан существующему запуску %s", task["id"], run_id)
            except Exception:
                log.exception("будильник #%s: захват сохранён для разбора", task["id"])

    def fire(self, task, invoke):
        room = self.room(task)
        if not self.tasks.claim_open(task["id"], kind=task.get("kind") or ""):
            return False

        def before(context):
            if context.delivery_chat_id != room:
                raise ValueError("alarm activation changed rooms")
            def link(item):
                item["claim"].update(helene_run_id=context.run_id, helene_room=room,
                                     helene_owner_stamp=self.continuity.owner_stamp())
            if not self._change_claim(task["id"], link):
                raise ValueError("alarm claim was cancelled before run creation")

        def after(context):
            self.continuity.verify_local_owner(context.run_id, context)

        try:
            with self.continuity.activation(before, after):
                invoke(room)
        finally:
            # Also handles no brain / exception before create. Called only while
            # the single runner thread is idle, never over a concurrent turn.
            self.reconcile_claims()
        return True
