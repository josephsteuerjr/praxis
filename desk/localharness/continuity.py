"""Подключение durable-механик ядра к одному последовательному циклу Hélène.

Политика продолжения, CAS, паузы и расписки остаются в ядре. Здесь только
локальная личность владельца, наблюдение начала и доставка в архив окна.
Недостающие крючки изолированы в install(): сохранение снимка и run,
наблюдение начала и проверка локального владельца. Telegram не подменяем.
"""
from __future__ import annotations

import functools
from contextlib import contextmanager
import hashlib
import json
import logging
from pathlib import Path

import transport
import owner_stop

log = logging.getLogger("helene.continuity")


class Continuity:
    def __init__(self, agent, desks, config_path: Path, activity, media_sender=None, telegram=None):
        self.agent, self.desks = agent, desks
        self.config_path = Path(config_path)
        self.activity = activity
        # Чем доставлять спуленный файл. Передаёт раннер (`runner.deliver_one_media`):
        # тело доставки одно на живой и на возобновлённый ход. None — доставщика нет,
        # и тогда очередь честно не разбирается, а не разбирается наполовину.
        self.media_sender = media_sender
        self.telegram = telegram
        self._activation = None

    def telegram_binding(self, room, principal):
        cfg = json.loads(self.config_path.read_text(encoding='utf-8-sig'))
        tg = cfg.get('telegram') or {}
        owner = str(tg.get('owner_id') or '').strip()
        if not owner.isdecimal() or int(owner) <= 0 or str(room) != owner or str(principal) != owner:
            raise ValueError('Telegram origin is not the current owner private conversation')
        material = [self.owner_stamp(), owner] + [str(tg.get(key) or '')
                    for key in ('mode','bot_token','api_id','api_hash')]
        return {'schema':'helene.telegram-authority.v1', 'owner_id':owner,
                'stamp':hashlib.sha256(json.dumps(material).encode()).hexdigest()}

    def verify_delivery_owner(self, run_id, context):
        room = str(context.delivery_chat_id or '')
        if transport.is_room(room):
            return self.verify_local_owner(run_id, context)
        channel, snapshot = self.agent._load_exact_run_channel(self.agent._runs(), context)
        expected = self.telegram_binding(room, context.principal_id)
        if (context.origin_chat_id != room or context.scope != 'owner'
                or not channel.owner or not channel.is_dm
                or snapshot['authority'].get('helene_telegram_owner') != expected):
            raise self.agent.DurableExecutionError('Telegram owner binding is absent or changed; delivery retained')

    def owner_stamp(self) -> str:
        # Повторная проверка при resume: смена владельца не наследует доверие
        # прежнего. Это provenance локальной установки, не Telegram-id.
        cfg = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        identity = {"installation": str(self.config_path.resolve()),
                    "owner": cfg.get("owner") or {}}
        return hashlib.sha256(json.dumps(identity, sort_keys=True,
                                        ensure_ascii=False).encode("utf-8")).hexdigest()

    def verify_local_owner(self, run_id: str, context) -> None:
        room = str(context.delivery_chat_id or "")
        channel, snapshot = self.agent._load_exact_run_channel(self.agent._runs(), context)
        binding = snapshot["authority"].get("helene_local_owner") or {}
        if (not transport.is_room(room) or context.origin_chat_id != room
                or context.scope != "owner" or context.principal_id != "unknown"
                or not channel.owner or binding.get("schema") != "helene.local-authority.v1"
                or binding.get("owner_stamp") != self.owner_stamp()):
            raise self.agent.DurableExecutionError(
                "local owner binding is absent or changed; explicit adoption is required")

    def install(self) -> None:
        agent = self.agent
        if getattr(agent, "_helene_continuity", None) is not None:
            raise RuntimeError("continuity adapter is already installed")
        original_create = agent._create_durable_run
        original_resume = agent.resume_durable_run
        original_authority = agent._AgentResumeRuntime._validate_current_authority
        original_snapshot = agent.run_snapshot.write
        manager = agent._runs()
        original_persist = manager.create

        @functools.wraps(original_snapshot)
        def snapshot(**kwargs):
            authority = kwargs["authority"]
            if transport.is_room(str(authority.get("delivery_chat_id") or "")) and authority.get("owner"):
                kwargs["authority"] = dict(authority, helene_local_owner={
                    "schema": "helene.local-authority.v1", "owner_stamp": self.owner_stamp()})
            elif authority.get('owner') and authority.get('is_dm'):
                kwargs['authority'] = dict(authority, helene_telegram_owner=self.telegram_binding(
                    authority.get('delivery_chat_id'), authority.get('principal_id')))
            return original_snapshot(**kwargs)

        @functools.wraps(original_persist)
        def persist(context, markdown):
            with owner_stop.LOCK:
                if self._activation is not None or context.kind != 'chat_turn':
                    owner_stop.require_admission()
                return persist_admitted(context, markdown)

        def persist_admitted(context, markdown):
            activation = self._activation
            if activation is not None and not activation["run_id"]:
                # Source stores the intended run id BEFORE the run exists.
                # A crash can be reconciled by that id without guessing from text.
                activation["before"](context)
                activation["run_id"] = context.run_id
            return original_persist(context, markdown)

        @functools.wraps(original_create)
        def create(**kwargs):
            ctx = kwargs["ctx"]
            run = original_create(**kwargs)
            activation = self._activation
            if activation is not None and activation["run_id"] == run.run_id:
                activation["after"](run)
            self.activity(run.run_id, str(ctx.chat_id or ""))
            return run

        @functools.wraps(original_resume)
        def resume(run_id):
            owner_stop.require_admission()
            context = agent._runs().context(run_id)
            self.activity(run_id, str(context.delivery_chat_id or ""))
            return original_resume(run_id)

        @functools.wraps(original_authority)
        def authority(runtime):
            context = runtime.plan.context
            if (transport.is_room(str(context.delivery_chat_id or ""))
                    and runtime.channel.owner and context.principal_id == "unknown"):
                self.verify_local_owner(runtime.plan.run_id, context)
                return
            if runtime.channel.owner and runtime.channel.is_dm:
                self.verify_delivery_owner(runtime.plan.run_id, context)
                return
            return original_authority(runtime)

        agent._create_durable_run = create
        agent.resume_durable_run = resume
        agent._AgentResumeRuntime._validate_current_authority = authority
        agent.run_snapshot.write = snapshot
        manager.create = persist
        agent._helene_continuity = self

    @contextmanager
    def activation(self, before, after=lambda run: None):
        """A source-to-run handoff in the runner's single execution thread."""
        if self._activation is not None:
            raise RuntimeError("nested source activation")
        self._activation = {"before": before, "after": after, "run_id": ""}
        try:
            yield self._activation
        finally:
            self._activation = None

    def deliver_pending_text(self) -> int:
        accepted = 0
        for plan in self.agent.run_pending_text_deliveries(limit=20):
            room = str(plan.get("conversation_id") or "")
            if not transport.is_room(room):
                if self.telegram is not None:
                    accepted += self.deliver_telegram_plan(plan)
                continue
            run_id = plan["run_id"]
            try:
                manager = self.agent._runs()
                context = manager.context(run_id)
                self.verify_delivery_owner(run_id, context)
                if context.delivery_chat_id != room:
                    raise ValueError("delivery room differs from run authority")
                manifest = manager.manifest(run_id)
                if ((manifest.get("control") or {}).get("action") in {"pause", "cancel"}
                        or manifest.get("status") in {"done", "failed", "cancelled"}):
                    continue
                desk = self.desks.get(room)
                self.activity(run_id, room)
                for chunk in plan.get("pending_chunks") or ():
                    # Архив — расписка приёма. Краш после fsync, но до WAL ядра
                    # повторно проецирует ту же расписку, не второе сообщение.
                    control = manager.manifest(run_id).get("control") or {}
                    if control.get("action") in {"pause", "cancel"}:
                        break
                    receipt = desk.deliver_once(str(chunk["text"]),
                                                key=str(chunk["delivery_key"]))
                    self.agent.run_delivery_text_chunk_accepted(
                        run_id, index=int(chunk["index"]),
                        delivery_key=str(chunk["delivery_key"]), message_id=receipt)
                    accepted += 1
                self.agent.run_delivery_text_reconcile(run_id)
                self.agent.run_delivery_finalize_recovered(run_id)
            except Exception:
                log.exception("локальная доставка ждёт подтверждения [%s]", run_id)
        return accepted

    def prepare_telegram_text(self, run_id, room, text, *, media_queue_ids=()):
        """Write the exact plan before any network call; the normal tick can resume it."""
        from botapi import _chunks, peer_thread
        peer, topic = peer_thread(str(room))
        chunks = [{'index':index,'text':part,'delivery_key':f'run:{run_id}:chunk:{index}',
                   'sha256':hashlib.sha256(part.encode()).hexdigest(),'reply_to':None}
                  for index,part in enumerate(_chunks(text))]
        plan = {'schema':self.agent._TELEGRAM_TEXT_PLAN_SCHEMA, 'conversation_id':str(room),
                'peer_id':str(peer),'topic_id':topic,'chunks':chunks}
        self.agent.run_delivery_started(run_id,chat_id=room,text_chars=sum(len(c['text']) for c in chunks),
                                        media_count=len(media_queue_ids),media_queue_ids=list(media_queue_ids),
                                        text_plan=plan)
        return {**plan,'run_id':run_id,'pending_chunks':chunks}

    def deliver_telegram_text(self, run_id, room, text):
        return self.deliver_telegram_plan(self.prepare_telegram_text(run_id,room,text))

    def deliver_telegram_plan(self, plan):
        from telegram_receipts import BotApiDeliveryRefused
        run_id, room = plan['run_id'], str(plan.get('conversation_id') or '')
        try:
            manager = self.agent._runs()
            context = manager.context(run_id)
            self.verify_delivery_owner(run_id,context)
            if context.delivery_chat_id != room:
                raise ValueError('Telegram delivery differs from run authority')
            bot = self.telegram() if self.telegram else None
            if bot is None or str(bot.owner_id) != room:
                raise ValueError('The bound owner Telegram transport is unavailable')
            for chunk in plan.get('pending_chunks') or ():
                manifest = manager.manifest(run_id)
                if ((manifest.get('control') or {}).get('action') in ('pause','cancel')
                        or manifest.get('status') in ('paused','done','failed','cancelled')):
                    return False
                self.verify_delivery_owner(run_id,context)
                receipt = bot.deliver_text(room,str(chunk['text']),str(chunk.get('reply_to') or ''),
                    run_id=run_id,reply_evidence=True,delivery_key=str(chunk['delivery_key']),receipt_id=True)
                if isinstance(receipt,self.agent.DirectSendRefusal):
                    raise BotApiDeliveryRefused(403)
                self.agent.run_delivery_text_chunk_accepted(run_id,index=int(chunk['index']),
                    delivery_key=str(chunk['delivery_key']),message_id=receipt)
            self.agent.run_delivery_text_reconcile(run_id)
            self.agent.run_delivery_finalize_recovered(run_id)
            return True
        except Exception as error:
            self.agent.run_delivery_failed(run_id,error)
            log.exception('Telegram result retained for inspection/recovery [%s]',run_id)
            return False

    def deliver_pending_media(self) -> int:
        """Файлы, которые ход поднял, а доставить не успел. -> сколько ушло.

        ⚑ ЗАЧЕМ ЭТО ЕСТЬ. В ядре очередь медиа разбирает исходящая граница mtproto. В
        издании её нет: харнесс поднимает свой транспорт, и спул не разбирает НИКТО.
        Пока ход шёл живьём, файл уезжал конвертом (`_deliver_outbound`); ход, поднятый
        заново, кладёт файл в спул — и до этой функции он оставался там навсегда, а прогон
        не мог терминализоваться и поднимался каждые 45 секунд.

        Порядок на каждый файл ровно такой и не иначе: доставить → записать durable-расписку
        → погасить предмет в спуле → досведение прогона. Расписка ДО гашения: если упадём
        между ними, предмет вернётся в очередь, а расписка не даст отправить второй раз.
        Обратный порядок терял бы доказательство доставки.
        """
        if self.media_sender is None:
            return 0
        sent = 0
        for plan in self.agent.run_pending_media_deliveries(limit=20):
            run_id = plan["run_id"]
            room = str(plan.get("conversation_id") or "")
            try:
                manager = self.agent._runs()
                context = manager.context(run_id)
                self.verify_delivery_owner(run_id, context)
                if context.delivery_chat_id != room:
                    raise ValueError("delivery room differs from run authority")
                spool = self.agent._media_spool()
                self.activity(run_id, room)
                for item in plan.get("items") or ():
                    control = manager.manifest(run_id).get("control") or {}
                    if control.get("action") in {"pause", "cancel"}:
                        break
                    # Политику спрашиваем ДО попытки: прогон мог уже получить расписку в
                    # прошлый заход, и повтор был бы вторым файлом человеку.
                    policy = self.agent.run_delivery_media_retry_policy(run_id, item.queue_id)
                    if policy in {"ack", "drop"}:
                        spool.discard(item.queue_id, receipt={"policy": policy})
                        continue
                    try:
                        self.verify_delivery_owner(run_id, context)
                        self.agent.run_delivery_media_started(run_id,item.queue_id)
                        receipt = self.media_sender(item, room)
                        if isinstance(receipt,getattr(self.agent,'DirectSendRefusal',())):
                            self.agent.run_delivery_media_result(run_id,item.queue_id,ok=False,permanent=True,
                                error=str(receipt),chat_id=room,path=str(item.path))
                            spool.discard(item.queue_id,receipt={'policy':'permanent-refusal'})
                            continue
                    except Exception as exc:
                        from telegram_receipts import BotApiDeliveryRefused
                        # Долг остаётся: предмет в очереди, расписки нет. Следующий заход
                        # попробует снова — и это правильнее, чем погасить долг по ошибке
                        # канала и оставить человека без обещанного файла.
                        self.agent.run_delivery_media_result(
                            run_id, item.queue_id, ok=False,
                            **({'permanent':True} if isinstance(exc,BotApiDeliveryRefused) else {}),
                            error=f"{type(exc).__name__}: {exc}",
                            chat_id=room, path=str(item.path),
                            caption=str(getattr(item, "caption", "") or ""))
                        log.exception("файл запуска не ушёл [%s / %s]", run_id, item.queue_id)
                        continue
                    self.agent.run_delivery_media_result(
                        run_id, item.queue_id, ok=True, message_id=str(receipt),
                        chat_id=room, path=str(item.path),
                        caption=str(getattr(item, "caption", "") or ""))
                    spool.discard(item.queue_id, receipt={"message_id": str(receipt)})
                    sent += 1
                    log.info("файл запуска доставлен [%s]: %s", run_id, str(receipt)[:120])
                self.agent.run_delivery_finalize_recovered(run_id)
            except Exception:
                log.exception("медиа запуска ждёт подтверждения [%s]", run_id)
        return sent

    def resume_due(self) -> list[dict]:
        self.deliver_pending_text()
        reports = self.agent.resume_durable_runs(limit=1)
        self.deliver_pending_text()
        # Медиа — ПОСЛЕ подъёма ходов: именно возобновлённый ход и кладёт файл в спул,
        # а до него разбирать нечего.
        self.deliver_pending_media()
        for report in reports:
            if report.get("status") not in {"noop", "not_resumable"}:
                log.info("продолжение [%s]: %s / %s / %s", report.get("run_id"),
                         report.get("plan_kind"), report.get("status"), report.get("reason", ""))
        return reports

    def closed_without_effects(self, run_id: str) -> bool:
        """Only the core may prove that a restart orphan never began work."""
        manager = self.agent._runs()
        if manager.manifest(run_id).get("status") != "cancelled":
            return False
        for row in manager.iter_events(run_id, reverse=True, strict=True):
            if row.get("kind") == "status_changed" and row.get("to_status") == "cancelled":
                return (row.get("control_action") == "cancel"
                        and row.get("requested_by") == "resume:evidence-free-orphan")
        return False
