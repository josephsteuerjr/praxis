# -*- coding: utf-8 -*-
"""Рука брокера: агент ПРОСИТ команду с правами, подписывает владелец (R7).

До этой руки брокер был дверью ровно для одной кнопки окна («Телефон»), а в
`localharness/` слова broker не встречалось ни разу. Тексты продукта при этом
говорили агенту про брокера как про существующий механизм — то есть обещали то,
чего у него не было. Здесь эта рука заводится.

## Как это работает

Своей двери к брокеру у агента нет: труба службы пускает по токену, а токен
лежит в `memory/.state/broker-token` с правами «СИСТЕМА, администраторы,
владелец». Поэтому агент не зовёт брокера, а пишет ПРОСЬБУ; оболочка её читает,
показывает владельцу родным окном Windows и только по «да» идёт в трубу своим
токеном. Обратно приходит квитанция — код возврата, оба потока, длительность.

Обмен — два файла в `memory/.state`, у каждого РОВНО ОДИН писатель, поэтому
замка не нужно (тот же приём, что у `mounts.json`):

    broker-asks.json      пишем мы, читает оболочка — чего просит агент;
    broker-answers.json   пишет оболочка, читаем мы — что решил владелец.

Контракт полей записан в шапке `common/broker.rs` рядом с `broker_asks_path` —
он один на обе стороны, и здесь повторяется только то, что нужно руке.

## Чего эта рука НЕ даёт, и врать об этом нельзя

* **Это не замок на агента.** В песочнице агент токена брокера не прочитает, а
  в интерактивном режиме — прочитает и может пойти в трубу сам, минуя окно
  подтверждения. Так написано и в `common/broker.rs`: брокер закрыт от ЧУЖИХ
  программ, а от собственного агента закрыт быть не может. Подтверждение
  владельца — честный и записываемый путь, а не преграда.
* **Ответ может не прийти вовсе.** Просьбу читает оболочка; нет окна — нет и
  читателя. Мы это проверяем ДО записи (блок `desk` в файле ответов плюс живой
  pid) и говорим агенту прямо, вместо того чтобы молчать до таймаута.
* **`exec` (права СИСТЕМЫ) заперт галочкой нулевой сессии** — её проверяет сама
  служба (`svc::broker_exec_allowed`). Владелец может сказать «да» в окне, а
  служба всё равно откажет: это два разных замка, и второй нам не подчиняется.
* **Просьба живёт десять минут.** Оболочка отклоняет всё, что старше
  (`BROKER_WISH_STALE_SEC`): подписывать вслепую просьбу, от которой агент уже
  ушёл дальше, опаснее, чем отказать. Значит рука ждёт минуты, а не часы, и
  «владелец не ответил» говорит именно этими словами — не «отказал».

Проверки просьбы здесь — копия проверок `BrokerAsk::parse` из
`common/broker.rs`. Не ради вежливости: что отвергает служба, владельцу даже не
показывают, и без местной проверки агент получал бы отказ через минуту ожидания
вместо ответа сразу.

Модуль без зависимостей, кроме стандартной библиотеки и соседей по харнессу.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("helene.broker")

#: Версия протокола обмена — `BROKER_V` в common/broker.rs.
V = 1

#: Сколько просьба живёт у оболочки (`BROKER_WISH_STALE_SEC`). Старше — отказ
#: без вопроса владельцу, поэтому и мы такие из своего файла убираем.
ASK_STALE_SEC = 10 * 60

#: Сколько рука ждёт ответа по умолчанию и сколько согласна ждать вообще.
#: Верх — меньше, чем живёт просьба: ждать дольше, чем она действительна, значит
#: обещать агенту ответ, которого уже не будет.
WAIT_DEFAULT = 120
WAIT_MAX = 300

#: Потолок ожидания САМОЙ КОМАНДЫ (`BROKER_TIMEOUT_MAX`).
TIMEOUT_MAX = 600
TIMEOUT_DEFAULT = 60

#: Сколько просьб держим в файле. У оболочки потолок разбора 64 строки; больше
#: тридцати ждущих просьб — это не работа, а очередь модальных окон владельцу.
KEEP_ASKS = 32

#: Двери брокера. `ping` ничего не выполняет.
OPS = ("ping", "spawn_interactive", "exec")

#: Что видно в анатомии. Заполняется на установке руки и на каждом её вызове.
STATE: dict = {"hand": False, "desk": "", "asks": 0, "answers": 0, "note": ""}


# --------------------------------------------------------------------------- #
#  Файлы обмена
# --------------------------------------------------------------------------- #

def asks_path(tree: Path) -> Path:
    return Path(tree) / "memory" / ".state" / "broker-asks.json"


def answers_path(tree: Path) -> Path:
    return Path(tree) / "memory" / ".state" / "broker-answers.json"


def _load(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _stamp() -> str:
    return time.strftime("%d.%m.%Y %H:%M")


def _rows(data: dict, key: str) -> list[dict]:
    return [r for r in (data.get(key) or ()) if isinstance(r, dict)]


# --------------------------------------------------------------------------- #
#  Проверки просьбы — копия границы из common/broker.rs
# --------------------------------------------------------------------------- #

def check_why(why: str) -> str:
    """Отказ словами или "" — если «зачем» годится. Это читает ВЛАДЕЛЕЦ."""
    if not why:
        return ("скажи ЗАЧЕМ тебе эта команда — эту строку читает владелец, и по "
                "ней он решает, подписывать или нет")
    if len(why) < 3 or not any(c.isalpha() for c in why):
        return "«зачем» должно быть словами, а не знаком-заглушкой"
    if len(why) > 500:
        return "«зачем» длиннее 500 знаков — это уже не объяснение"
    if any(ord(c) < 32 or ord(c) == 127 for c in why):
        # Журнал владельца читается построчно: перевод строки внутри «зачем»
        # нарисовал бы в нём десяток чужих записей с любым текстом.
        return "«зачем» — одной строкой: перевод строки в нём подделывает журнал"
    return ""


def check_cmd(cmd: str) -> str:
    """Отказ словами или "" — если программа названа так, как примет служба.

    Только полный путь: `CreateProcess` ищет голое имя СНАЧАЛА в папке своего
    процесса, а это папка установки. Подложенный туда `netsh.exe` исполнился бы
    правами СИСТЕМЫ.
    """
    if not cmd:
        return "нет команды: назови программу полным путём в поле cmd"
    if any(c in cmd for c in ("\0", "\n", "\r")):
        return "в имени программы перевод строки или нулевой байт"
    drive = len(cmd) > 2 and cmd[0].isascii() and cmd[0].isalpha() \
        and cmd[1] == ":" and cmd[2] in "\\/"
    if not drive and not cmd.startswith("\\\\"):
        return (f"команду надо называть полным путём, а не «{cmd}»: голое имя "
                f"Windows ищет сначала в папке программы, и подменённый там файл "
                f"исполнился бы правами СИСТЕМЫ. Пример: "
                f"C:\\Windows\\System32\\netsh.exe")
    if ".." in cmd:
        return "в пути есть «..» — назови программу прямо"
    return ""


def check_args(args) -> str:
    """Отказ словами или "" — если аргументы массивом строк, как их ждёт служба."""
    if args is None:
        return ""
    if isinstance(args, str) or not isinstance(args, (list, tuple)):
        return ("args — МАССИВ строк, и никогда одна строка: склейка командной "
                "строки это класс дыр, а не удобство. "
                "[\"advfirewall\", \"firewall\", \"show\", \"rule\"]")
    if len(args) > 256:
        return "больше 256 аргументов — это не команда"
    for item in args:
        if not isinstance(item, str):
            return "в args есть не-строка — аргументы бывают только строками"
        if "\0" in item:
            return "в args есть нулевой байт — Windows обрежет команду на нём"
    return ""


def check(op: str, cmd: str, args, why: str, timeout_sec: int) -> str:
    """Вся граница разом. "" — просьбу можно записывать."""
    if op not in OPS:
        return (f"не знаю такой двери: «{op}». Бывают ping (проверка связи), "
                f"spawn_interactive (правами владельца, в его сессии) и "
                f"exec (правами СИСТЕМЫ, только при включённой нулевой сессии)")
    said = check_why(why)
    if said:
        return said
    said = check_args(args)
    if said:
        return said
    if op != "ping":
        said = check_cmd(cmd)
        if said:
            return said
    if not isinstance(timeout_sec, int) or isinstance(timeout_sec, bool):
        return "timeout_sec — это число секунд"
    if timeout_sec < 1 or timeout_sec > TIMEOUT_MAX:
        return f"timeout_sec бывает от 1 до {TIMEOUT_MAX} с"
    return ""


# --------------------------------------------------------------------------- #
#  Рука
# --------------------------------------------------------------------------- #

class Broker:
    """Просьбы агента к брокеру и ответы владельца. Один экземпляр на харнесс."""

    def __init__(self, tree: Path, cfg: dict | None = None):
        self.tree = Path(tree)
        self.cfg = dict(cfg or {})

    # --- чтение ------------------------------------------------------------- #

    def answers(self) -> list[dict]:
        return _rows(_load(answers_path(self.tree)), "answers")

    def asks(self) -> list[dict]:
        return _rows(_load(asks_path(self.tree)), "requests")

    def desk(self) -> tuple[bool, str]:
        """Есть ли кому показать просьбу. -> (слушают ли, слова для агента).

        Блока `desk` нет или файла нет вовсе — окна нет, и просьба не дождётся
        никого. Pid проверяем отдельно: файл ответов переписывается не по
        сердцебиению, и вчерашняя запись про закрытое окно выглядит как живая.
        """
        data = _load(answers_path(self.tree))
        block = data.get("desk")
        if not isinstance(block, dict):
            return False, ("окна Hélène нет: просьбу к брокеру некому показать "
                           "владельцу. Скажи ему словами — брокера открывает "
                           "окно, а не я")
        pid = block.get("pid")
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            pid = 0
        if pid > 0:
            try:
                import boot
                if not boot._pid_alive(pid):
                    return False, ("окно Hélène закрыто (последним его слушал "
                                   f"процесс {pid}) — просьбу к брокеру некому "
                                   "показать владельцу. Скажи ему словами")
            except Exception:
                log.debug("живость окна не спросилась", exc_info=True)
        since = str(block.get("watching_since") or "").strip()
        return True, (f"окно слушает просьбы с {since}" if since else "окно слушает просьбы")

    def service(self) -> bool | None:
        """Стоит ли служба. None — спросить не у кого. Только для честных слов."""
        try:
            import modes
            return modes.service_installed(self.cfg)
        except Exception:
            log.debug("служба не спросилась", exc_info=True)
            return None

    # --- запись ------------------------------------------------------------- #

    def save(self, rows: list[dict]) -> None:
        path = asks_path(self.tree)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(".tmp-" + path.name)
            tmp.write_text(json.dumps({"v": V, "updated_at": _stamp(),
                                       "requests": rows},
                                      ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("брокер: просьба не записана (%s): %s", path, exc)
            return
        self._shut_out(path)

    def _shut_out(self, path: Path) -> None:
        """Закрыть файл обмена от контейнера, если ограда поднята.

        Файлы лежат в `memory`, а она выдана контейнеру на изменение. Без этого
        шага агент своей же командой `shell` переписал бы себе ответ владельца —
        прав это ему не даст (настоящую дверь держит оболочка), но обманет его
        самого, а «прибор врёт» здесь дороже всего. Права ставим ПОСЛЕ записи:
        запись идёт через os.replace, и новый файл приходит с правами папки.
        """
        try:
            import fence
            if fence.state().get("container"):
                fence.shut_out_container(Path(path), share_read=True)
        except Exception:
            log.debug("файл брокера не закрыт от песочницы", exc_info=True)

    def forget_answered(self) -> list[dict]:
        """Убрать просьбы, на которые уже ответили, и протухшие. -> что осталось."""
        rows = self.asks()
        answered = {str(r.get("id") or "") for r in self.answers()}
        now = time.time()
        left = []
        for row in rows:
            if str(row.get("id") or "") in answered:
                continue
            try:
                at = float(row.get("at_unix") or 0)
            except (TypeError, ValueError):
                at = 0.0
            if at and now - at > ASK_STALE_SEC:
                continue
            left.append(row)
        if len(left) != len(rows):
            self.save(left)
        return left

    # --- просьба ------------------------------------------------------------ #

    def ask(self, op: str, cmd: str, args, why: str, timeout_sec: int,
            wait_sec: int) -> str:
        why = " ".join(str(why or "").split())
        cmd = str(cmd or "").strip()
        if isinstance(args, str):
            # Отдельная строка про строку вместо массива: это самая частая
            # ошибка вызова, и общий отказ читался бы как «мне нельзя».
            return "брокер: " + check_args(args)
        args = list(args or [])
        said = check(op, cmd, args, why, timeout_sec)
        if said:
            return f"брокер: {said}. Просьбу не записал — служба отвергла бы её же"
        listening, words = self.desk()
        if not listening:
            return (f"брокер: {words}. Просьбу не записал: она протухнет через "
                    f"{ASK_STALE_SEC // 60} минут, так и не показавшись никому")
        rows = self.forget_answered()
        if len(rows) >= KEEP_ASKS:
            return (f"брокер: у владельца уже {len(rows)} неотвеченных просьб — "
                    f"новые не записываю. Дождись ответа или скажи ему словами")
        ask_id = _new_id()
        rows.append({"id": ask_id, "op": op, "cmd": cmd, "args": args, "why": why,
                     "timeout_sec": int(timeout_sec), "at_unix": int(time.time()),
                     "at": _stamp()})
        self.save(rows)
        STATE["asks"] = len(rows)
        log.info("брокер: просьба агента %s (%s): %s · %s", ask_id, op, why,
                 " ".join([cmd] + [str(a) for a in args])[:300])
        answer = self.wait(ask_id, wait_sec)
        if answer is None:
            return (f"брокер: владелец не ответил за {wait_sec} с. Это НЕ отказ: "
                    f"просьба {ask_id} ещё висит и протухнет через "
                    f"{ASK_STALE_SEC // 60} минут с записи. Посмотреть ответ "
                    f"позже — broker_request(action=\"list\"). Торопит — скажи "
                    f"владельцу словами, окно ждать не умеет")
        return "брокер: " + describe_answer(answer)

    def wait(self, ask_id: str, wait_sec: int) -> dict | None:
        """Дождаться ответа на свою просьбу. None — не дождались."""
        deadline = time.time() + max(0, int(wait_sec))
        while True:
            for row in self.answers():
                if str(row.get("id") or "") == ask_id:
                    # Оболочка только что переписала файл ответов через rename,
                    # то есть принесла новый файл с правами папки: сужение,
                    # поставленное на установке, на нём не живёт. Ставим снова
                    # ровно в тот момент, когда оно потерялось.
                    self._shut_out(answers_path(self.tree))
                    self.forget_answered()
                    return row
            if time.time() >= deadline:
                return None
            time.sleep(0.7)

    # --- список ------------------------------------------------------------- #

    def listing(self) -> str:
        rows = self.forget_answered()
        answers = self.answers()[-8:]
        listening, words = self.desk()
        installed = self.service()
        out = [words if listening else f"⚠ {words}"]
        out.append({True: "служба установлена — брокер есть",
                    False: "службы нет — повышать права некому, любая просьба "
                           "получит отказ",
                    None: "стоит ли служба, спросить не у кого"}[installed])
        if rows:
            out.append("Ждут владельца: " + "; ".join(
                f"{r.get('id')} — {r.get('why')}" for r in rows))
        else:
            out.append("Ждущих просьб нет.")
        if answers:
            out.append("Последние ответы:")
            out += ["  " + describe_answer(r) for r in answers]
        else:
            out.append("Ответов ещё не было.")
        return "\n".join(out)


def _new_id() -> str:
    """Свой на просьбу, из знаков, которые примет оболочка ([A-Za-z0-9_-])."""
    return time.strftime("%H%M%S") + "-" + os.urandom(3).hex()


def describe_answer(row: dict) -> str:
    """Квитанция человеческими словами. Не «успех», а что именно вышло."""
    decision = str(row.get("decision") or "")
    head = {"allowed": "владелец разрешил", "refused": "ОТКАЗ",
            "failed": "разрешено, но не вышло"}.get(decision, decision or "ответ")
    parts = [f"{row.get('id')}: {head}"]
    note = str(row.get("note") or "").strip()
    if note:
        parts.append(note)
    if "code" in row:
        code = row.get("code")
        parts.append("код возврата не пришёл — команда не завершилась"
                     if code is None else f"код возврата {code}")
    for key, title in (("out", "вывод"), ("err", "ошибки")):
        text = str(row.get(key) or "").strip()
        if text:
            parts.append(f"{title}: {text[:2000]}")
    if row.get("ms") is not None:
        parts.append(f"{row.get('ms')} мс")
    return " · ".join(parts)


# --------------------------------------------------------------------------- #
#  Схема руки и выдача её агенту
# --------------------------------------------------------------------------- #

TOOL = {
    "name": "broker_request",
    "description": (
        "Попросить владельца выполнить одну команду с правами, которых у тебя "
        "нет. Ты не «становишься системой»: просьбу читает владелец в окне "
        "Windows, видит саму команду и твоё «зачем», и только по его «да» её "
        "выполняет служба. Отказ тоже записывается.\n"
        "action=ask — попросить: op = spawn_interactive (правами владельца, в "
        "его сессии) | exec (правами СИСТЕМЫ; работает, только если владелец "
        "включил галочку нулевой сессии) | ping (проверка связи, ничего не "
        "выполняет); cmd — программа ПОЛНЫМ путём (C:\\Windows\\System32\\"
        "netsh.exe); args — массив строк, никогда одна строка; why — зачем, "
        "одной строкой, это читает владелец.\n"
        "action=list — что ждёт ответа и что уже ответили.\n"
        "Брокера держит служба Windows: не поставлена — придёт отказ. Окно "
        "закрыто — просьбу некому показать, и я скажу об этом сразу. Ответ "
        "приходит минуты; «владелец не ответил» — это не «отказал»."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["ask", "list"],
                   "description": "ask — попросить, list — ждущие просьбы и ответы"},
        "op": {"type": "string", "enum": list(OPS),
               "description": "ping | spawn_interactive (правами владельца) | "
                              "exec (правами СИСТЕМЫ)"},
        "cmd": {"type": "string", "description": "программа полным путём"},
        "args": {"type": "array", "items": {"type": "string"},
                 "description": "аргументы массивом строк"},
        "why": {"type": "string", "description": "зачем, одной строкой — это читает владелец"},
        "timeout_sec": {"type": "integer",
                        "description": f"сколько ждать саму команду, 1..{TIMEOUT_MAX} с"},
        "wait_sec": {"type": "integer",
                     "description": f"сколько ждать ответа владельца, 0..{WAIT_MAX} с"},
    }},
}


def _hand(broker: "Broker"):
    def broker_request(action: str = "ask", op: str = "spawn_interactive",
                       cmd: str = "", args=None, why: str = "",
                       timeout_sec: int = TIMEOUT_DEFAULT,
                       wait_sec: int = WAIT_DEFAULT) -> str:
        try:
            action = str(action or "ask").strip().lower()
            if action == "list":
                return broker.listing()
            if action != "ask":
                return "broker_request: action бывает ask или list"
            try:
                timeout_sec = int(timeout_sec)
            except (TypeError, ValueError):
                return "broker_request: timeout_sec — это число секунд"
            try:
                wait_sec = max(0, min(WAIT_MAX, int(wait_sec)))
            except (TypeError, ValueError):
                wait_sec = WAIT_DEFAULT
            return broker.ask(str(op or "").strip().lower(), cmd, args, why,
                              timeout_sec, wait_sec)
        except Exception as exc:                      # рука не роняет ход
            log.exception("broker_request упал")
            return f"broker_request: не вышло — {exc}"

    return broker_request


def install(agent_mod, tree: Path, cfg: dict | None = None) -> None:
    """Выдать агенту руку брокера. Ничего не роняет: не вышло — записано почему.

    Приём тот же, что у `fence._offer_mount_hand`: дерево — код владельца, его
    править нельзя, поэтому схема дописывается в `BASE_TOOLS`, а исполнение — в
    `TOOL_IMPL`. Без записи в `BASE_TOOLS` модель об этой руке просто не узнает.
    """
    broker = Broker(Path(tree), cfg)
    impl = getattr(agent_mod, "TOOL_IMPL", None)
    if not isinstance(impl, dict):
        log.warning("брокер: рука не выдана — у дерева нет TOOL_IMPL")
        return
    if impl.get(TOOL["name"]) is None:
        impl[TOOL["name"]] = _hand(broker)
        tools = getattr(agent_mod, "BASE_TOOLS", None)
        if isinstance(tools, list) and not any(
                isinstance(t, dict) and t.get("name") == TOOL["name"] for t in tools):
            tools.append(dict(TOOL))
    listening, words = broker.desk()
    left = broker.forget_answered()
    # Файл ответов пишет оболочка, и каждый её `rename` приносит новый файл с
    # правами папки: сужение живёт до следующего ответа, поэтому ставим его и
    # здесь, и после каждой своей записи.
    broker._shut_out(answers_path(Path(tree)))
    STATE.update({"hand": True, "desk": words, "asks": len(left),
                  "answers": len(broker.answers()),
                  "note": ("просьбы показывает окно; служба выполняет их "
                           "квитанцией" if listening else
                           "просить сейчас некого — окна нет")})
    log.info("брокер: рука broker_request выдана агенту · %s · ждущих просьб %d",
             words, len(left))


def state() -> dict:
    return dict(STATE)
