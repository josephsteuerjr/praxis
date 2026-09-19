# -*- coding: utf-8 -*-
"""Тело для руки `computer`: мост и тело Праксис живут рядом с харнессом.

Рука `computer` в дереве (`tree/agent.py::tool_computer`) — КЛИЕНТ: она ходит
по HTTP в `praxis-bridge`, а тот по вебсокету передаёт команду в `praxis-body`,
который и водит окнами (UIA через COM, `uia.rs`), экраном, клавиатурой и
мышью, читает и пишет файлы, запускает процессы. До 06.09 в поставке Hélène
не было ни моста, ни тела — только исходники `tree/body`, и рука отвечала
«unavailable» в любом режиме.

Теперь оба едут в поставке (`helene-bridge.exe`, `helene-body.exe`, сборка
`installer/build_dist.py` из `live/body/crates`; на macOS — `helene-bridge`,
`helene-body` из `build_mac.py`), а поднимает их ЭТОТ модуль —
детьми самого раннера, а не оболочки. Почему не оболочка, хотя план 06.09
предлагал её: под службой харнесс поднимает не `helene.exe`, а
`helene-svc.exe session-host` (задача планировщика в сессии владельца,
`svc/src/main.rs::session_task_create_args`), и тело, поднятое оболочкой, под
службой не поднялось бы вовсе. Раннер — единственный процесс, который есть в
обоих путях, и он же — единственный, кому нужен токен.

Токены — ТОЛЬКО В ПАМЯТИ. На каждый старт генерируются два (device для тела,
controller для руки), мост и тело получают их через свою среду, а рука — через
подмену `body_client._settings`. В `os.environ` раннера они не кладутся: среду
раннера наследует каждая команда `shell`, и токен управления телом уехал бы в
любой `set` агента. На диске токенов нет: `data/body/body.json` — без поля
`token` (тело читает его из `PRAXIS_BODY_TOKEN`, `config.rs::load`).

Дети живут в job-объекте раннера с KILL_ON_JOB_CLOSE: умер раннер (штатно или
нет) — умерли и они, мост не остаётся держать порт. Сторож перезапускает
упавших с растущей паузой и раз в четверть минуты спрашивает тело
`body.status` через мост — это единственное честное «тело подключено», и
оно пишется в `memory/.state/body.json` для окна и телефона.

macOS (порт 19.09). Тело и мост те же крейты, собранные под darwin
(`helene-bridge`, `helene-body` — без `.exe`, в корне поставки, `build_mac.py`).
Job-объекта на POSIX нет: дети поднимаются в своей группе процессов
(`start_new_session`) и гасятся группой (`killpg`: SIGTERM, через пять секунд
SIGKILL), а от аварии движка их сторожит их собственный сторож родителя
(`mac::watch_parent` в теле). Окнами тело водит через Accessibility, экраном —
через CoreGraphics, и для обоих системе нужны два разрешения TCC — «Запись
экрана» и «Универсальный доступ». Их состояние тело отдаёт в `desktop.status`
(`tcc`, `hints`, `platform`), а сторож кладёт в снимок `body.json` — не чаще,
чем идёт проба `body.status`; окно рисует по ним две строки и кнопки «Открыть
настройки». Описание тула `computer` у дерева написано под Windows (PowerShell,
UI Automation); на darwin оно правится подстрочными заменами поверх
загруженного модуля (`mac_tool_text`) — файлы дерева не трогаем, это код
Праксис.

Права. У дерева права на действия руки уже есть: `_COMPUTER_ACTION_SCOPES`
(action → один из четырёх скоупов `computer_access.SCOPES`), но решает их
`computer_access.allowed(actor, scope)` по Telegram-id владельца
(`PRAXIS_OWNER_ID`), которого у Hélène нет: ход из окна идёт с `ctx.owner=True`
и без числового принципала, и дерево отвечало бы «нет выданного права для
этого Telegram id». Здесь право решает ВЛАДЕЛЕЦ галочками в helene.json:

    "computer": {"enabled": true, "port": <DEFAULT_PORT>,
                 "scopes": ["computer.read", "computer.files",
                            "computer.process", "computer.apps"]}

Список перечитывается на каждый вызов руки (по mtime файла): снял галочку в
Настройках — следующий вызов уже отказан, перезапуск не нужен. Сама рука
остаётся рукой дерева — обёртка здесь только спрашивает разрешение владельца,
называет причину отказа и говорит про тело, которое ещё не подключилось.
"""
from __future__ import annotations

import atexit
import ctypes
import datetime as dt
import json
import logging
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

# Типы Windows API — только на Windows. Структуры job-объекта ниже описаны на
# уровне модуля и ссылаются на `wt.DWORD`, поэтому на POSIX подставляются те же
# ctypes-типы (приём из `fence.py`): модуль обязан импортироваться на darwin,
# где job-объекта нет, а дети живут группой процессов.
if os.name == "nt":
    import ctypes.wintypes as wt  # noqa: E402 — только на Windows
else:
    class _WinTypesOnPosix:
        """Имена wintypes теми же ctypes-типами — чтобы структуры описались."""

        DWORD = ctypes.c_uint32
        BOOL = ctypes.c_int
        UINT = ctypes.c_uint
        HANDLE = ctypes.c_void_p
        LPCWSTR = ctypes.c_wchar_p

    wt = _WinTypesOnPosix()

log = logging.getLogger("helene.body")

#: Ключ блока в helene.json.
KEY = "computer"
#: Порт моста по умолчанию. НЕ 9473: на нём у автора живёт тело самой Праксис.
DEFAULT_PORT = 9480
#: Сколько портов вверх пробуем, если умолчание занято.
PORT_SPAN = 20
#: Четыре права дерева — в том порядке, в котором их показывает окно.
SCOPES = ("computer.read", "computer.files", "computer.process", "computer.apps")


def exe_name(base: str) -> str:
    """Имя исполняемого по платформе: на Windows с `.exe`, на POSIX — как есть."""
    return f"{base}.exe" if os.name == "nt" else base


#: Мост и тело в корне поставки; на macOS — те же имена без `.exe`
#: (`build_mac.py` кладёт их рядом с бандлами).
BRIDGE_EXE = exe_name("helene-bridge")
BODY_EXE = exe_name("helene-body")
#: Тело есть на Windows (UIA через COM) и с 19.09 на macOS (Accessibility и
#: CoreGraphics). На прочих POSIX его нет: ничего не поднимается, секции тела в
#: снимке нет (`runner._computer_state` → None), тул `computer` снимается из
#: набора. Та же правда со стороны каталога — `modes.HAS_COMPUTER`.
HAS_BODY = os.name == "nt" or sys.platform == "darwin"
#: Спрашивать ли у тела `desktop.status` ради разрешений системы (TCC): только
#: там, где они есть. На Windows проба остаётся одной — `body.status`.
ASKS_TCC = sys.platform == "darwin"
#: Имя устройства, когда у машины нет имени: как в умолчании самого клиента
#: дерева на Windows и своё на Mac.
DEFAULT_DEVICE = "windows-pc" if os.name == "nt" else "mac"
#: Снимок для окна и телефона (сторож пишет его раз в несколько секунд).
STATE_FILE = ("memory", ".state", "body.json")
#: Токен устройства для ОКНА, когда движок поднят службой (macOS, §6 плана
#: 19.09). Под демоном launchd движок живёт вне графической сессии: WindowServer
#: его не видит, TCC ему ничего не выдаст — тело оттуда не работает. Поэтому он
#: поднимает только МОСТ и кладёт сюда ключ устройства, а тело поднимает окно
#: (`shell::watch_service_body`) и подаёт ключ телу через `PRAXIS_BODY_TOKEN`.
#: Файл перезаписывается на КАЖДОМ старте движка и закрыт правами 0600.
TOKEN_FILE = ("memory", ".state", "body-token")


def under_service() -> bool:
    """Поднят ли движок службой: демон launchd ставит детям `HELENE_SERVICE=1`.

    Только на darwin. На Windows эту переменную не ставит никто: там служба
    поднимает интерактивную половину задачей планировщика — В СЕССИИ владельца,
    с рабочим столом, и тело у неё работает как обычно. Читать переменную и там
    значило бы менять проверенное поведение ради платформы, которой это не
    касается.
    """
    return sys.platform == "darwin" and os.environ.get("HELENE_SERVICE") == "1"

_CREATE_NO_WINDOW = 0x08000000
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

#: Состояние — одно на процесс; анатомия, ограда и рука читают его отсюда.
STATE: dict = {
    "enabled": False,
    "available": False,     # есть ли exe тела и моста рядом с программой
    "reason": "не поднималось",
    "port": 0,
    "device": "",
    "scopes": [],
    "bridge_pid": 0,
    "body_pid": 0,
    "connected": None,      # None — ещё не спрашивали; False — мост есть, тела нет
    # Движок поднят службой: тело поднимает окно, а не он (macOS). Ключ есть
    # всегда, чтобы форма снимка не зависела от того, дошло ли дело до подъёма.
    "service": False,
    "identity": {},
    "checked_at": "",
    "logs": [],
}
if ASKS_TCC:
    # Разрешения системы — из `desktop.status` тела (`tcc`, `hints`, `platform`);
    # None/пусто — ещё не спрашивали или тело их не отдаёт. На Windows этих
    # ключей в снимке нет: там их некому заполнить, а пустое поле читалось бы
    # окном как «разрешений нет».
    STATE.update({"tcc": None, "hints": [], "platform": ""})

_BODY: "Body | None" = None
_TOKENS: dict = {}          # url, controller, device — читает подменённый _settings


# --------------------------------------------------------------------------- #
#  Конфиг
# --------------------------------------------------------------------------- #

def block(cfg: dict) -> dict:
    raw = cfg.get(KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def enabled(cfg: dict) -> bool:
    return bool(block(cfg).get("enabled", False))


def scopes(cfg: dict) -> list[str]:
    """Права, выданные владельцем. Нет ключа `scopes` — все четыре (включил
    опцию — получил руку целиком; сузить можно галочками в Настройках)."""
    raw = block(cfg).get("scopes")
    if raw is None:
        return list(SCOPES)
    if not isinstance(raw, (list, tuple)):
        return []
    return [s for s in SCOPES if s in {str(x) for x in raw}]


def port(cfg: dict) -> int:
    try:
        value = int(block(cfg).get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return value if 1024 <= value <= 65535 else DEFAULT_PORT


def device_id() -> str:
    """Имя устройства для тела: имя машины, как его видит владелец.

    Ограничения `config.rs::validate`: 1..128 знаков, без управляющих и
    `/ \\ ? #`. Имя машины под них подходит; пустое — `DEFAULT_DEVICE`
    (`windows-pc`, как в умолчании самого клиента, на Mac — `mac`). На macOS
    `gethostname()` отдаёт `Имя.local` — хвост `.local` владельцу ни о чём."""
    raw = str(os.environ.get("COMPUTERNAME") or socket.gethostname() or "").strip()
    if sys.platform == "darwin" and raw.lower().endswith(".local"):
        raw = raw[:-len(".local")]
    safe = re.sub(r"[\x00-\x1f/\\?#]+", "-", raw).strip("-").lower()
    return safe[:128] or DEFAULT_DEVICE


# --------------------------------------------------------------------------- #
#  Сборка на диске: где exe
# --------------------------------------------------------------------------- #

def _exe_pair(install_root: Path) -> tuple[Path, Path] | None:
    """Мост и тело рядом с программой; для стенда — `HELENE_BODY_DIR`."""
    candidates = []
    override = os.environ.get("HELENE_BODY_DIR")
    if override:
        candidates.append(Path(override))
    candidates.append(Path(install_root))
    for base in candidates:
        for bridge_name, body_name in ((BRIDGE_EXE, BODY_EXE),
                                       (exe_name("praxis-bridge"), exe_name("praxis-body"))):
            bridge, body = base / bridge_name, base / body_name
            if bridge.is_file() and body.is_file():
                return bridge, body
    return None


def _port_free(value: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        try:
            probe.bind(("127.0.0.1", value))
        except OSError:
            return False
    return True


def pick_port(wanted: int) -> int | None:
    """Первый свободный порт от `wanted` вверх. Занятый порт — чужая программа
    (или тело Праксис на 9473): свой мост туда не ставим и в чужой не ходим."""
    for value in range(wanted, min(wanted + PORT_SPAN, 65535) + 1):
        if _port_free(value):
            return value
    return None


# --------------------------------------------------------------------------- #
#  Job-объект: дети умирают вместе с раннером
# --------------------------------------------------------------------------- #

class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in
                ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                 "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wt.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wt.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wt.DWORD),
        ("SchedulingClass", wt.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
    k.CreateJobObjectW.restype = wt.HANDLE
    k.SetInformationJobObject.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD]
    k.SetInformationJobObject.restype = wt.BOOL
    k.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    k.AssignProcessToJobObject.restype = wt.BOOL
    k.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
    k.TerminateJobObject.restype = wt.BOOL
    k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k.OpenProcess.restype = wt.HANDLE
    k.CloseHandle.argtypes = [wt.HANDLE]
    k.CloseHandle.restype = wt.BOOL
    return k


def _make_job():
    """Задание с KILL_ON_JOB_CLOSE. None — не вышло; тогда дети гасятся
    руками при выходе, а после аварии раннера остаются сиротами (записано)."""
    if os.name != "nt":
        return None
    try:
        k = _kernel32()
        job = k.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                         ctypes.byref(info), ctypes.sizeof(info)):
            log.warning("тело: заданию не поставлен KILL_ON_JOB_CLOSE (код %d)",
                        ctypes.get_last_error())
        return job
    except Exception:
        log.warning("тело: задание для детей не создалось", exc_info=True)
        return None


def _adopt(job, proc: subprocess.Popen) -> None:
    if not job or os.name != "nt":
        return
    try:
        k = _kernel32()
        handle = int(proc._handle)  # type: ignore[attr-defined]
        if not k.AssignProcessToJobObject(job, handle):
            log.warning("тело: %s не приписан к заданию (код %d) — после аварии "
                        "движка может остаться сиротой", proc.args[0],
                        ctypes.get_last_error())
    except Exception:
        log.warning("тело: не приписал ребёнка к заданию", exc_info=True)


# --------------------------------------------------------------------------- #
#  Дети: мост и тело
# --------------------------------------------------------------------------- #

def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class _Child:
    def __init__(self, name: str, argv: list[str], env: dict, cwd: Path, log_path: Path):
        self.name = name
        self.argv = argv
        self.env = env
        self.cwd = cwd
        self.log_path = log_path
        self.proc: subprocess.Popen | None = None
        self.falls: list[float] = []
        self.retry_at = 0.0
        self.started_at = 0.0

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def spawn(self, job) -> bool:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            # Лог не растёт без границы: больше 5 МБ — прежний в .1.
            try:
                if self.log_path.stat().st_size > 5 * 1024 * 1024:
                    os.replace(self.log_path, self.log_path.with_suffix(".log.1"))
            except OSError:
                pass
            out = open(self.log_path, "ab")
            self.proc = subprocess.Popen(
                self.argv, env=self.env, cwd=str(self.cwd), stdin=subprocess.DEVNULL,
                stdout=out, stderr=subprocess.STDOUT, **spawn_kwargs())
            out.close()
            _adopt(job, self.proc)
            self.started_at = time.monotonic()
            log.info("тело: %s поднят, pid %d", self.name, self.proc.pid)
            return True
        except Exception as exc:
            self.proc = None
            log.warning("тело: %s не поднялся: %s", self.name, exc)
            return False

    def kill(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        if os.name != "nt":
            kill_group(proc)
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def spawn_kwargs(posix: bool | None = None) -> dict:
    """Чем поднимать ребёнка на этой платформе.

    Windows — без окна консоли (`CREATE_NO_WINDOW`), группу держит job-объект.
    POSIX — своя сессия и группа процессов (`start_new_session`): job-объекта
    нет, и остановить тело вместе с его внуками можно только группой
    (`kill_group`). Параметр — для стенда: обе ветки разбираются на любой ОС.
    """
    if (os.name != "nt") if posix is None else posix:
        return {"start_new_session": True}
    return {"creationflags": _CREATE_NO_WINDOW}


def kill_group(proc: subprocess.Popen, *, grace: float = 5.0) -> None:
    """POSIX: погасить ребёнка группой — SIGTERM, через `grace` секунд SIGKILL.

    Ребёнок — лидер своей группы (`start_new_session`), значит `killpg` по его
    pid накрывает и внуков. Тело, которому дали договорить (SIGTERM), закрывает
    вебсокет и файлы штатно; не вышел за срок — SIGKILL всей группе, чтобы мост
    не остался держать порт после движка.
    """
    if proc.poll() is not None:
        return
    killpg = getattr(os, "killpg", None)
    if killpg is None:
        try:
            proc.kill()
        except Exception:
            pass
        return
    try:
        killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        log.debug("тело: SIGTERM группе %s не ушёл", proc.pid, exc_info=True)
    try:
        proc.wait(timeout=grace)
    except Exception:
        try:
            killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=2)
        except Exception:
            pass
    # Лидер вышел, а группа живёт, пока жив её последний процесс: внуки, которые
    # SIGTERM проигнорировали, добиваются отдельно. Пустая группа даёт ESRCH.
    try:
        killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass


class Body:
    """Мост + тело под сторожем. Один на раннер."""

    def __init__(self, install_root: Path, tree: Path, cfg: dict):
        self.install_root = Path(install_root)
        self.tree = Path(tree)
        self.home = self.tree / "body"
        self.cfg = cfg
        self.job = None
        self.children: list[_Child] = []
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.port = 0
        self.device = device_id()
        self.device_token = ""
        self.controller_token = ""
        #: Движок поднят службой (демон launchd): тело поднимает окно, не мы.
        self.service = under_service()

    # ---- подъём ---------------------------------------------------------- #

    def start(self) -> bool:
        pair = _exe_pair(self.install_root)
        if pair is None:
            STATE.update({"available": False,
                          "reason": f"в поставке нет тела: рядом с программой не найдены "
                                    f"{BRIDGE_EXE} и {BODY_EXE}"})
            log.warning("тело: %s", STATE["reason"])
            return False
        bridge_exe, body_exe = pair
        STATE["available"] = True
        chosen = pick_port(port(self.cfg))
        if chosen is None:
            STATE["reason"] = (f"порты {port(self.cfg)}…{port(self.cfg) + PORT_SPAN} заняты — "
                               f"мосту негде слушать; смени computer.port в helene.json")
            log.warning("тело: %s", STATE["reason"])
            return False
        if chosen != port(self.cfg):
            log.warning("тело: порт %d занят другой программой — мост берёт %d",
                        port(self.cfg), chosen)
        self.port = chosen
        self.device_token = secrets.token_urlsafe(32)
        self.controller_token = secrets.token_urlsafe(32)
        state_dir = self.home / "state"
        bridge_dir = self.home / "bridge"
        for d in (self.home, state_dir, bridge_dir):
            d.mkdir(parents=True, exist_ok=True)
        # Конфиг тела — БЕЗ токена: его тело берёт из PRAXIS_BODY_TOKEN.
        body_json = self.home / "body.json"
        body_json.write_text(json.dumps({
            "device_id": self.device,
            "bridge_ws_url": f"ws://127.0.0.1:{self.port}",
            "artifact_base_url": f"http://127.0.0.1:{self.port}",
            "state_dir": str(state_dir),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        base_env = {k: v for k, v in os.environ.items()
                    if not k.startswith("PRAXIS_BODY") and not k.startswith("PRAXIS_BRIDGE")}
        bridge_env = dict(base_env, PRAXIS_BRIDGE_DEVICE_TOKEN=self.device_token,
                          PRAXIS_BRIDGE_CONTROLLER_TOKEN=self.controller_token,
                          RUST_LOG=base_env.get("HELENE_BODY_RUST_LOG", "praxis_bridge=info,tower_http=warn"))
        body_env = dict(base_env, PRAXIS_BODY_TOKEN=self.device_token,
                        RUST_LOG=base_env.get("HELENE_BODY_RUST_LOG", "praxis_body=info"))
        self.children = [
            _Child("мост", [str(bridge_exe), "--listen", f"127.0.0.1:{self.port}",
                            "--state-dir", str(bridge_dir)],
                   bridge_env, self.home, self.home / "bridge.log"),
        ]
        # Под службой тело поднимает ОКНО, а не движок: у процесса вне
        # графической сессии нет ни рабочего стола, ни разрешений TCC, и
        # поднятое отсюда тело честно отказывало бы на каждый вызов.
        if self.service:
            self.write_device_token()
            log.info("тело: движок поднят службой — поднимаю только мост; тело подключится, "
                     "когда откроется окно %s", "Helene")
        else:
            self.children.append(
                _Child("тело", [str(body_exe), "connect", "--config", str(body_json)],
                       body_env, self.home, self.home / "body.log"))
        self.job = _make_job()
        _TOKENS.update({"url": f"http://127.0.0.1:{self.port}",
                        "controller": self.controller_token, "device": self.device})
        STATE.update({"port": self.port, "device": self.device,
                      "logs": [str(c.log_path) for c in self.children],
                      "service": self.service,
                      "reason": (f"мост 127.0.0.1:{self.port}; тело подключится, когда откроется "
                                 f"окно Helene — движок поднят службой, и рабочего стола у него нет"
                                 if self.service else
                                 f"мост 127.0.0.1:{self.port}, тело подключается")})
        for child in self.children:
            child.spawn(self.job)
        STATE["bridge_pid"] = self.children[0].proc.pid if self.children[0].proc else 0
        # Под службой второго ребёнка нет вовсе — не «его pid ноль», а «его
        # здесь не поднимают». Обращение по индексу роняло бы сторож.
        STATE["body_pid"] = (self.children[1].proc.pid
                             if len(self.children) > 1 and self.children[1].proc else 0)
        self.thread = threading.Thread(target=self._watch, name="helene-body", daemon=True)
        self.thread.start()
        atexit.register(self.stop)
        self._write_state()
        return True

    def token_path(self) -> Path:
        return self.tree.joinpath(*TOKEN_FILE)

    def write_device_token(self) -> None:
        """Положить ключ устройства для окна: 0600, заново на каждом старте.

        Заново — потому что ключ живёт ровно столько, сколько этот мост: окно,
        прочитавшее вчерашний, получило бы тело, которому мост отвечает «чужой».
        Права сужаем ДО записи (`os.open` с 0o600): создать файл открытым, а
        потом закрыть — это окно, в которое ключ уже видно.

        ⚠ Режим в `os.open` действует ТОЛЬКО на создание. Временный файл от
        прошлого запуска (упали между `open` и `replace`) уже существует — и
        тогда `O_CREAT` прав не меняет, а `O_TRUNC` пишет ключ в файл с теми
        правами, какие там были, хоть 0644. Поэтому `fchmod` по уже открытому
        дескриптору: сужает права именно тому файлу, в который пишем, и до
        того, как в нём что-то появилось (гонку через имя это тоже закрывает).
        """
        path = self.token_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(".tmp-" + path.name)
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                try:
                    os.fchmod(fd, 0o600)
                except (AttributeError, OSError):
                    # Windows: режимов POSIX там нет, права даёт ACL папки данных.
                    pass
                os.write(fd, self.device_token.encode("utf-8"))
            finally:
                os.close(fd)
            os.replace(tmp, path)
        except OSError as exc:
            # Молчать нельзя: без ключа окно тело не поднимет, и снаружи это
            # выглядит как «тул computer сломался».
            log.warning("тело: ключ устройства не записался в %s: %s — окно не сможет "
                        "поднять тело под службой", path, exc)

    def drop_device_token(self) -> None:
        """Убрать ключ: мост ушёл, и ключ к нему больше ничего не открывает.
        Оставленный, он заставлял бы окно поднимать тело в пустоту."""
        try:
            self.token_path().unlink(missing_ok=True)
        except OSError:
            log.debug("тело: ключ устройства не убрался", exc_info=True)

    # ---- сторож ---------------------------------------------------------- #

    def _watch(self) -> None:
        last_probe = 0.0
        last_prune = time.monotonic()
        while not self.stopping.wait(3.0):
            now = time.monotonic()
            if now - last_prune >= SPOOL_PRUNE_EVERY_SEC:
                last_prune = now
                pruned = prune_spool(self.home / "bridge" / "spool.db")
                if pruned.get("frames") or pruned.get("responses"):
                    log.info("тело: спул моста подчищен — кадров %s, ответов %s",
                             pruned.get("frames"), pruned.get("responses"))
            for child in self.children:
                if child.alive():
                    continue
                if child.proc is not None:
                    code = child.proc.poll()
                    child.proc = None
                    child.falls = [t for t in child.falls if now - t < 600] + [now]
                    pause = min(60.0, 2.0 ** min(len(child.falls), 6))
                    child.retry_at = now + pause
                    log.warning("тело: %s завершился (код %s) — поднимаю снова через %.0f с; "
                                "лог %s", child.name, code, pause, child.log_path)
                    STATE["connected"] = None
                    STATE["reason"] = f"{child.name} упал (код {code}), перезапуск"
                    continue
                if now >= child.retry_at:
                    child.spawn(self.job)
            STATE["bridge_pid"] = self.children[0].proc.pid if self.children[0].alive() else 0
            STATE["body_pid"] = (self.children[1].proc.pid
                                 if len(self.children) > 1 and self.children[1].alive() else 0)
            # Пробы: первые полминуты — часто (тело подключается секунды), потом
            # раз в 15 с. Проба и есть правда о «подключено».
            young = any(now - c.started_at < 30 for c in self.children if c.alive())
            if now - last_probe >= (3.0 if young and not STATE.get("connected") else 15.0):
                last_probe = now
                self.probe(timeout=4.0)
            self._write_state()

    def spool_bytes(self) -> int:
        try:
            return (self.home / "bridge" / "spool.db").stat().st_size
        except OSError:
            return 0

    def probe(self, *, timeout: float = 4.0) -> bool:
        """Спросить тело через мост. Пишет `connected`/`identity`/`reason`."""
        result = call("body.status", {}, timeout=timeout)
        ok = bool(result.get("ok")) and isinstance(result.get("identity"), dict)
        STATE["checked_at"] = _now()
        if ok:
            identity = result.get("identity") or {}
            STATE["identity"] = {k: identity.get(k) for k in
                                 ("kind", "session_id", "integrity", "elevated")}
            if not STATE.get("connected"):
                log.info("тело: подключено — %s, сессия %s, %s", identity.get("kind"),
                         identity.get("session_id"), identity.get("integrity"))
            STATE["connected"] = True
            STATE["reason"] = (f"тело живо: мост 127.0.0.1:{self.port}, сессия "
                               f"{identity.get('session_id')}, {identity.get('kind')}")
            if ASKS_TCC:
                self.probe_desktop(timeout=timeout)
        else:
            STATE["connected"] = False
            why = str(result.get("error") or result.get("code") or "нет ответа")
            bridge_alive = self.children[0].alive() if self.children else False
            STATE["reason"] = ("мост не отвечает" if not bridge_alive else
                               "мост есть, тело ещё не подключилось") + f" ({why})"
        return ok

    def probe_desktop(self, *, timeout: float = 4.0) -> dict:
        """Разрешения системы — из `desktop.status` тела (macOS: `tcc`, `hints`,
        `platform`). Зовётся из `probe` и только вслед за удачным `body.status`:
        не чаще его и ни разу — без тела. Ответ без `tcc` (тело старее движка)
        оставляет `None`: окно тогда строк про разрешения не рисует, а не
        показывает «нет» там, где не спрашивали."""
        result = call("desktop.status", {}, timeout=timeout)
        tcc = result.get("tcc") if result.get("ok") else None
        if isinstance(tcc, dict):
            STATE["tcc"] = {"screen_recording": bool(tcc.get("screen_recording")),
                            "accessibility": bool(tcc.get("accessibility"))}
            hints = result.get("hints")
            STATE["hints"] = ([str(h) for h in hints if str(h).strip()]
                              if isinstance(hints, (list, tuple)) else [])
            STATE["platform"] = str(result.get("platform") or "")
        elif result.get("ok"):
            STATE["tcc"] = None
            STATE["hints"] = []
            STATE["platform"] = str(result.get("platform") or "")
        return result

    def _write_state(self) -> None:
        path = self.tree.joinpath(*STATE_FILE)
        STATE["spool_bytes"] = self.spool_bytes()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(".tmp-" + path.name)
            tmp.write_text(json.dumps(state(), ensure_ascii=False, indent=2),
                           encoding="utf-8", newline="\n")
            os.replace(tmp, path)
        except OSError:
            log.debug("тело: снимок состояния не записался", exc_info=True)

    # ---- остановка ------------------------------------------------------- #

    def stop(self) -> None:
        if self.stopping.is_set():
            return
        self.stopping.set()
        if self.service:
            self.drop_device_token()
        for child in self.children:
            child.kill()
        if self.job:
            try:
                _kernel32().TerminateJobObject(self.job, 0)
            except Exception:
                pass
        STATE.update({"connected": None, "bridge_pid": 0, "body_pid": 0,
                      "reason": "остановлено вместе с движком"})
        try:
            self._write_state()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  Спул моста: подчистка (мост копит ответы для контроллера, который их не ack'ает)
# --------------------------------------------------------------------------- #

#: Раз в столько секунд сторож подчищает `data/body/bridge/spool.db`.
SPOOL_PRUNE_EVERY_SEC = 600


def prune_spool(path: Path, *, frames_older_min: int = 10,
                responses_older_min: int = 60) -> dict:
    """Убрать из спула моста то, что никто никогда не прочитает.

    Замер 06.09 на установке владельца: за вечер с рукой `computer` спул вырос
    до 5 МБ — 615 кадров `to_controller` и 611 ответов, по 2 МБ каждые. Мост
    (`tree/body/crates/praxis-bridge`) хранит кадры для контроллера до его
    подтверждения по WebSocket, а наш контроллер — HTTP-опрос (`body_client`):
    он читает ответ из таблицы `responses` и ничего не подтверждает, поэтому
    кадры `to_controller` в этом продукте не читает никто и никогда, а сам
    мост чистит их только через 30 дней. Ответы (`responses`, terminal=1)
    нужны, пока `body_client.call` их опрашивает — секунды, не часы.

    Это наш процесс и наша база (мост — ребёнок раннера, файл — в дереве
    агента), поэтому чистим сами, а не просим мост менять протокол. Журнал
    БД — DELETE (`PRAGMA journal_mode` в spool.rs), одновременная запись моста
    ждёт нашего замка не дольше `timeout`. Файл не усыхает (VACUUM здесь не
    зовём — мост держит соединение), но освобождённые страницы переиспользуются:
    рост ограничен окном в `frames_older_min`/`responses_older_min`.

    -> {"frames": удалено кадров, "responses": удалено ответов} либо {"error"}.
    """
    import sqlite3
    if not path.is_file():
        return {"frames": 0, "responses": 0}
    try:
        con = sqlite3.connect(str(path), timeout=2.0)
        try:
            with con:
                frames = con.execute(
                    "DELETE FROM frames WHERE direction='to_controller' "
                    "AND datetime(created_at) < datetime('now', ?)",
                    (f"-{int(frames_older_min)} minutes",)).rowcount
                responses = con.execute(
                    "DELETE FROM responses WHERE terminal=1 "
                    "AND datetime(updated_at) < datetime('now', ?)",
                    (f"-{int(responses_older_min)} minutes",)).rowcount
        finally:
            con.close()
    except sqlite3.Error as exc:
        log.debug("тело: спул не подчищен (%s): %s", path, exc)
        return {"frames": 0, "responses": 0, "error": str(exc)}
    return {"frames": int(frames), "responses": int(responses)}


# --------------------------------------------------------------------------- #
#  Клиент: подмена настроек body_client (токен не в среде)
# --------------------------------------------------------------------------- #

def _settings() -> tuple[str, str, str]:
    return (str(_TOKENS.get("url") or ""), str(_TOKENS.get("controller") or ""),
            str(_TOKENS.get("device") or DEFAULT_DEVICE))


def call(capability: str, args: dict | None = None, *, timeout: float = 30.0) -> dict:
    """Вызов через клиент дерева, если он уже загружен; иначе — своим urllib.

    Сторож зовёт это до и без импорта `agent` (тесты, ранний старт), поэтому
    у него своя короткая дорога: submit + опрос расписки, как в `body_client.call`.
    """
    if not _TOKENS.get("controller"):
        return {"ok": False, "code": "unavailable", "error": "тело не поднято"}
    mod = sys.modules.get("body_client")
    if mod is not None and getattr(mod, "_helene_settings", False):
        try:
            return mod.call(capability, args or {}, execution="interactive", timeout=timeout)
        except Exception as exc:
            return {"ok": False, "code": "client", "error": str(exc)}
    return _raw_call(capability, args or {}, timeout=timeout)


#: Открыватель БЕЗ прокси: мост — сосед по этой же машине (127.0.0.1), и
#: системный прокси ему не дорога, а стена. 15.09: у пользователя с Psiphon
#: проба уезжала в прокси, тело числилось отключённым при живом теле. В дереве
#: это закрывает рычаг `sitecustomize` (петля мимо прокси), но СТОРОЖ стартует
#: раньше: `body.launch` в `runner.py` стоит до `_load_tree`, где рычаг и
#: встаёт. Своя короткая дорога — со своим открывателем.
_DIRECT: "object | None" = None


def _direct_opener():
    global _DIRECT
    if _DIRECT is None:
        import urllib.request
        _DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return _DIRECT


def _raw_request(method: str, path: str, payload: dict | None, timeout: float) -> dict:
    import urllib.error
    import urllib.request
    url, token, _device = _settings()
    raw = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url + path, data=raw, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with _direct_opener().open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return {"ok": False, "code": f"http_{exc.code}",
                    **json.loads(exc.read().decode("utf-8", "replace"))}
        except Exception:
            return {"ok": False, "code": f"http_{exc.code}", "error": str(exc)}
    except (OSError, ValueError) as exc:
        return {"ok": False, "code": "transport", "error": f"{type(exc).__name__}: {exc}"}


def _raw_call(capability: str, args: dict, *, timeout: float) -> dict:
    import urllib.parse
    _url, _token, device = _settings()
    rid = secrets.token_hex(16)
    started = _raw_request(
        "POST", f"/v1/controller/{urllib.parse.quote(device, safe='')}/invoke",
        {"request_id": rid, "operation_id": f"op-{rid}", "execution": "interactive",
         "capability": capability, "args": args}, timeout=min(timeout, 10.0))
    if not started.get("ok"):
        return started
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        seen = _raw_request(
            "GET", f"/v1/controller/{urllib.parse.quote(device, safe='')}/requests/{rid}",
            None, timeout=min(10.0, max(1.0, deadline - time.monotonic())))
        frame = seen.get("response") if seen.get("ok") else None
        if isinstance(frame, dict):
            kind = str(frame.get("type") or "")
            if kind == "error":
                return {"ok": False, "code": frame.get("code"), "error": frame.get("message")}
            if kind == "result":
                result = frame.get("result") if isinstance(frame.get("result"), dict) else {}
                return {**frame, **result, "ok": bool(frame.get("ok"))}
        if not seen.get("ok"):
            return seen
        time.sleep(0.15)
    return {"ok": False, "code": "timeout", "error": f"нет ответа тела за {timeout:.0f} с"}


# --------------------------------------------------------------------------- #
#  Подъём из раннера
# --------------------------------------------------------------------------- #

def launch(install_root: Path, tree: Path, cfg: dict) -> "Body | None":
    """Поднять мост и тело, если владелец включил опцию. Ничего не роняет."""
    global _BODY
    STATE["enabled"] = enabled(cfg)
    STATE["scopes"] = scopes(cfg)
    if not HAS_BODY:
        # Тела в этой сборке нет по замыслу порта: ничего не поднимаем и не
        # тревожим — это состав платформы, а не сбой. Снимок не пишем: секции
        # тела для окна нет вовсе, и слова о платформе на экран не едут.
        STATE.update({"available": False, "reason": "тела в этой сборке нет",
                      "connected": None, "bridge_pid": 0, "body_pid": 0})
        log.info("тело: в этой сборке его нет — тул computer откажет словами")
        return None
    pair = _exe_pair(Path(install_root))
    STATE["available"] = pair is not None
    if not STATE["enabled"]:
        STATE["reason"] = ("выключено владельцем: Настройки → «Управление компьютером»"
                           if pair is not None else
                           f"выключено; и тела в поставке нет ({BRIDGE_EXE}, {BODY_EXE})")
        log.info("тело: %s", STATE["reason"])
        _write_static_state(Path(tree))
        return None
    if not STATE["scopes"]:
        STATE["reason"] = ("включено, но ни одного права не выдано — рука откажет; "
                           "галочки в Настройках → «Управление компьютером»")
        log.warning("тело: %s", STATE["reason"])
    body = Body(Path(install_root), Path(tree), cfg)
    if not body.start():
        _write_static_state(Path(tree))
        return None
    _BODY = body
    return body


def _write_static_state(tree: Path) -> None:
    path = Path(tree).joinpath(*STATE_FILE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state(), ensure_ascii=False, indent=2),
                        encoding="utf-8", newline="\n")
    except OSError:
        pass


def shutdown() -> None:
    if _BODY is not None:
        _BODY.stop()


def state() -> dict:
    return dict(STATE, checked_at=STATE.get("checked_at") or "", written_at=_now())


def windows_truth() -> str:
    """Одна честная строка про окна — в анатомию и на экран «Система»."""
    if not HAS_BODY:
        # Секции тела в картине нет — и строки про окна тоже: пустую строку
        # окно не рисует, а «нет на этой платформе» на экран не едет.
        return ""
    if not STATE.get("enabled"):
        if STATE.get("available"):
            return ("окна: рука `computer` выключена владельцем — тело в поставке есть, "
                    "включается в Настройках, карточка «Управление компьютером»")
        return f"окна: тела в поставке нет ({BRIDGE_EXE}, {BODY_EXE}) и опция выключена"
    if not STATE.get("available"):
        return f"окна: опция включена, но тела в поставке нет ({BRIDGE_EXE}, {BODY_EXE})"
    rights = ", ".join(STATE.get("scopes") or []) or "ни одного права"
    return (f"окна: тело поднято кодом агента снаружи ограды (мост 127.0.0.1:{STATE.get('port')}, "
            f"{STATE.get('reason')}); права владельца: {rights}{tcc_words()}")


def tcc_words() -> str:
    """Хвост строки про окна на macOS: каких разрешений системы не хватает.

    Пусто, когда все есть или ещё не спрашивали: «не спрашивали» и «нет» —
    разные ответы, и второй словами не подменяет первый.
    """
    tcc = STATE.get("tcc")
    if not isinstance(tcc, dict):
        return ""
    missing = [name for key, name in (("screen_recording", "«Запись экрана»"),
                                      ("accessibility", "«Универсальный доступ»"))
               if not tcc.get(key)]
    if not missing:
        return ""
    return ("; системе не хватает разрешений: " + ", ".join(missing)
            + " (Системные настройки → Конфиденциальность и безопасность)")


# --------------------------------------------------------------------------- #
#  Рука: разрешение владельца поверх руки дерева
# --------------------------------------------------------------------------- #

SCOPE_WORDS = {
    "computer.read": "смотреть (состояние, инвентарь, список файлов)",
    "computer.files": "файлы (читать, писать, пересылать)",
    "computer.process": "процессы (запускать команды и следить за ними)",
    "computer.apps": "окна и ввод (экран, клавиатура, мышь, буфер обмена)",
}


class _LiveScopes:
    """Права владельца — перечитываются по mtime helene.json на каждый вызов."""

    def __init__(self, config_path: Path | None, initial: dict):
        self.path = Path(config_path) if config_path else None
        self.stamp = None
        self.enabled = enabled(initial)
        self.scopes = scopes(initial)

    def refresh(self) -> None:
        if self.path is None:
            return
        try:
            st = self.path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            return
        if stamp == self.stamp:
            return
        try:
            import boot
            cfg = json.loads(boot.read_config_text(self.path))
        except Exception:
            try:
                cfg = json.loads(self.path.read_text(encoding="utf-8-sig"))
            except Exception:
                log.debug("тело: helene.json не перечитался — права прежние", exc_info=True)
                return
        if not isinstance(cfg, dict):
            return
        self.stamp = stamp
        self.enabled = enabled(cfg)
        self.scopes = scopes(cfg)
        STATE["scopes"] = list(self.scopes)


def _owner_turn(agent_mod) -> bool:
    """Ход владельца: окно или его Telegram-id — так раннер помечает ctx.owner."""
    try:
        ctx = agent_mod._TURN_CHANNEL.get()
    except Exception:
        return False
    return bool(ctx is not None and getattr(ctx, "owner", False))


#: Списки схем тулов у дерева, из которых снимаем `computer` в сборке без тела.
#: `computer` живёт в `OWNER_TOOLS`, но списки берём с запасом: пусть дерево
#: переложит его в другой — снимется всё равно. Пустых/отсутствующих не боимся.
_TOOL_LISTS = ("BASE_TOOLS", "OWNER_TOOLS", "PRAXIS_SELF_TOOLS", "SHARED_CONTEXT_TOOLS",
               "TOOLS", "ABSENCE_TOOLS", "FAMILY_TOOLS", "WORKSHOP_TOOLS", "FORGE_TOOLS")


def _install_absent(agent_mod) -> None:
    """Сборка без тела (прочие POSIX): рука `computer` СНИМАЕТСЯ вовсе, а не отвечает
    словами. Приём тот же, что у брокера (`broker.install` при `not HAS_BROKER`):
    обещать модели тул, который всегда откажет, хуже, чем не иметь его. Раньше тул
    оставался в наборе с ответом-заглушкой — модель видела `computer`, звала его и
    получала отказ, а экран «Система» рисовал его «вне ограды» виндовым текстом
    про галочки «Управление компьютером», которых на macOS нет.

    Снимаем из `TOOL_IMPL` (исполнение) и из всех списков схем (что видит модель).
    Права — всегда «нет»: давать их некому. Из `hands_report` руку убирает сама
    ограда, спросив `body.HAS_BODY`.
    """
    if callable(getattr(agent_mod, "_computer_allowed", None)):
        agent_mod._computer_allowed = lambda scope: False
    impl = getattr(agent_mod, "TOOL_IMPL", None)
    if isinstance(impl, dict):
        impl.pop("computer", None)
    for attr in _TOOL_LISTS:
        lst = getattr(agent_mod, attr, None)
        if isinstance(lst, list):
            lst[:] = [t for t in lst
                      if not (isinstance(t, dict) and t.get("name") == "computer")]
    log.info("тело: тул computer снят из набора — в этой сборке тела нет")


def install(agent_mod, tree: Path, cfg: dict, config_path: Path | None = None) -> None:
    """Подключить руку `computer` к телу и к разрешению владельца.

    Зовётся ПОСЛЕ `fence.install` и после импорта дерева: подменяется
    `body_client._settings` (адрес, токен, устройство — из памяти, не из среды),
    `agent._computer_allowed` (галочки владельца вместо Telegram-id) и
    `agent._is_sovereign_actor` (ход владельца — суверенный: иначе `write`,
    `replace` и `execution=system` отказывали бы хозяину из его же окна).
    Сама рука остаётся рукой дерева; обёртка только называет отказ словами.
    """
    if not HAS_BODY:
        _install_absent(agent_mod)
        return
    live = _LiveScopes(config_path, cfg)
    try:
        import body_client
        if not getattr(body_client, "_helene_settings", False):
            body_client._settings = _settings
            body_client._helene_settings = True
    except Exception:
        log.warning("тело: body_client не загрузился — рука без тела", exc_info=True)

    def _computer_allowed(scope: str) -> bool:
        live.refresh()
        return bool(live.enabled) and scope in live.scopes

    sovereign_original = getattr(agent_mod, "_is_sovereign_actor", None)
    if callable(sovereign_original) and not getattr(sovereign_original, "_helene_owner", False):
        def _is_sovereign_actor() -> bool:
            try:
                if sovereign_original():
                    return True
            except Exception:
                pass
            return _owner_turn(agent_mod)
        _is_sovereign_actor._helene_owner = True  # type: ignore[attr-defined]
        _is_sovereign_actor.__doc__ = getattr(sovereign_original, "__doc__", "")
        agent_mod._is_sovereign_actor = _is_sovereign_actor
    if callable(getattr(agent_mod, "_computer_allowed", None)):
        agent_mod._computer_allowed = _computer_allowed

    impl = getattr(agent_mod, "TOOL_IMPL", None)
    original = impl.get("computer") if isinstance(impl, dict) else None
    if not callable(original) or getattr(original, "_helene_body", False):
        return
    action_scopes = dict(getattr(agent_mod, "_COMPUTER_ACTION_SCOPES", {}) or {})

    def computer(*args, **kwargs):
        action = str(kwargs.get("action") or (args[0] if args else "") or "").strip().lower()
        live.refresh()
        if not live.enabled:
            where = ("тело в поставке есть, включается в Настройках — карточка "
                     "«Управление компьютером»" if STATE.get("available") else
                     f"и тела в поставке нет ({BRIDGE_EXE}, {BODY_EXE} рядом с программой)")
            return (f"Рука окон выключена владельцем: {where}. Ограда здесь ни при чём — "
                    "это отдельная опция, она не зависит от режима.")
        required = action_scopes.get(action)
        if action == "observe" and (kwargs.get("path") or (len(args) > 1 and args[1])):
            required = "computer.files"
        if required and required not in live.scopes:
            given = ", ".join(live.scopes) or "ни одного"
            return (f"Владелец не выдал право `{required}` — {SCOPE_WORDS.get(required, required)}. "
                    f"Выдано: {given}. Галочки — в Настройках, карточка «Управление "
                    f"компьютером»; уговаривать меня бесполезно, решает он.")
        if not STATE.get("available") or _BODY is None:
            logs = ", ".join(STATE.get("logs") or []) or "нет"
            return (f"Опция включена, но тело не поднялось: {STATE.get('reason')}. "
                    f"Логи: {logs}")
        if STATE.get("connected") is not True and action != "status":
            # Быстрая проба перед отказом: тело подключается секунды, а сторож
            # ходит раз в 3–15 с.
            if not _BODY.probe(timeout=3.0):
                return (f"Тело ещё не подключилось к мосту: {STATE.get('reason')}. "
                        f"Мост pid {STATE.get('bridge_pid') or '—'}, тело pid "
                        f"{STATE.get('body_pid') or '—'}; логи: "
                        + ", ".join(STATE.get("logs") or []) + ". Повтори через несколько "
                        "секунд или спроси `action=status`.")
        return original(*args, **kwargs)

    computer._helene_body = True
    computer.__name__ = getattr(original, "__name__", "computer")
    computer.__doc__ = getattr(original, "__doc__", "")
    impl["computer"] = computer
    if sys.platform == "darwin":
        describe_for_mac(agent_mod)
        speak_mac(agent_mod)
    log.info("тело: рука computer подключена — %s", windows_truth())


# --------------------------------------------------------------------------- #
#  macOS: описание тула `computer` у дерева написано под Windows
# --------------------------------------------------------------------------- #

#: Подстрочные замены в схеме тула `computer` для darwin — в том порядке, в
#: каком идут. Описание у дерева одно на все платформы и говорит про PowerShell,
#: UI Automation и Win32; модель на Mac читала бы обещания, которых тело не
#: сдержит (и звала бы `run` с командами PowerShell). Замены — по подстрокам,
#: а не переписыванием: описание меняется у неё часто, и полная копия здесь
#: отставала бы молча. Подстрока, которой в тексте нет, просто не срабатывает.
MAC_TOOL_TEXT: tuple[tuple[str, str], ...] = (
    ("Use the connected Windows computer", "Use the connected computer (macOS)"),
    ("run/poll/stop manage PowerShell processes", "run/poll/stop manage shell processes (zsh)"),
    ("native interactive-desktop hands (no Office COM)", "native desktop hands (Accessibility)"),
    ("coding path on Windows (no wcode proxy task needed; receipts bind to your current run "
     "automatically)", "coding path on this computer (receipts bind to your current run automatically)"),
    ("; .ps1/.psm1/.psd1 with non-ASCII text get a UTF-8 BOM so PowerShell 5.1 parses them", ""),
    # ⚠ «reads the UI Automation control tree» здесь БЫЛО и ничего не находило: в живом
    # описании стоит «returns the …» (`helene/core/agent.py`, схема `computer`). Мёртвая
    # пара не безобидна — она выглядит как забота о фразе, которой на самом деле никто не
    # правит, и следующий читатель верит ей вместо того, чтобы проверить. Стенд
    # `tests/t_tool_text_mac.py` теперь краснеет на каждую такую.
    ("returns the UI Automation control tree", "returns the Accessibility control tree"),
    ("It goes through UI Automation patterns", "It goes through Accessibility actions"),
    ("many controls (WinForms TextBox) select all text on focus", "some controls select all text on focus"),
    ("one Win32 notch", "one wheel notch"),
)

#: То же для тула `computer_access` — корня доверия к руке `computer`. Он свой отдельный
#: тул со своим описанием, и Windows в нём ровно одно слово; держать его в общем словаре
#: нельзя: стенд `t_tool_text_mac.py` сверяет КАЖДУЮ пару `MAC_TOOL_TEXT` с живым
#: описанием тула `computer`, и пара, которой там нечего искать, читалась бы как мёртвая.
MAC_ACCESS_TOOL_TEXT: tuple[tuple[str, str], ...] = (
    ("Owner-only root of trust for Windows access",
     "Owner-only root of trust for computer access"),
)


def mac_tool_text(text: str) -> str:
    """Текст схемы `computer` словами macOS. Чистая функция, идемпотентна."""
    for old, new in MAC_TOOL_TEXT:
        text = text.replace(old, new)
    return text


def mac_access_tool_text(text: str) -> str:
    """Текст схемы `computer_access` словами macOS. Чистая функция, идемпотентна."""
    for old, new in MAC_ACCESS_TOOL_TEXT:
        text = text.replace(old, new)
    return text


def _mac_walk(node, say=None) -> None:
    """Те же замены по всем `description` схемы, на любой глубине."""
    say = say or mac_tool_text
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                node[key] = say(value)
            else:
                _mac_walk(value, say)
    elif isinstance(node, list):
        for item in node:
            _mac_walk(item, say)


#: Какому тулу каким словарём править описание. `computer_access` здесь потому, что он
#: говорит «for Windows access» на машине, где Windows нет: корень доверия к руке
#: `computer` обязан называть ту же вещь тем же словом, что и сама рука.
_MAC_TOOL_TEXTS = {"computer": mac_tool_text, "computer_access": mac_access_tool_text}


def describe_for_mac(agent_mod) -> int:
    """Поправить описания тулов `computer` и `computer_access` во всех списках дерева.

    Правится ЗАГРУЖЕННЫЙ модуль, не файл: дерево — код Праксис, его файлы не
    трогаем (тот же приём, что у `install` с `TOOL_IMPL`). Один и тот же словарь
    схемы может лежать в нескольких списках — правки идемпотентны, повтор
    безвреден. -> сколько схем тронуто.
    """
    seen: set[int] = set()
    for attr in _TOOL_LISTS:
        lst = getattr(agent_mod, attr, None)
        if not isinstance(lst, list):
            continue
        for tool in lst:
            if not isinstance(tool, dict) or id(tool) in seen:
                continue
            say = _MAC_TOOL_TEXTS.get(tool.get("name"))
            if say is None:
                continue
            seen.add(id(tool))
            _mac_walk(tool, say)
    if seen:
        log.info("тело: описания тулов computer/computer_access переведены на слова macOS "
                 "(схем: %d)", len(seen))
    return len(seen)


# --------------------------------------------------------------------------- #
#  macOS: указатель руки и блок владельца в кадре тоже написаны под Windows
# --------------------------------------------------------------------------- #
#
# Живой случай 20.09 (Mac, 0.8.1): на просьбу подвинуть мышь агент ответил «на маке
# не реализовано», не позвав тул. Схему `computer` describe_for_mac уже переводил,
# но модель читает не схему: с указателями рук (PRAXIS_TOOLS_POINTERS=on, издание
# 0.7.1) она видит строку «Yegor's Windows computer: files, PowerShell, …»
# (`tool_text_en.POINTER_PURPOSE`, по-русски — `agent.HAND_PURPOSE`), а в блоке
# владельца кадра — «The Windows PC is your DIRECT body … run/poll/stop PowerShell».
# Оба текста — правда для сервера Праксис и для Windows-издания, и ложь на Mac.
# Правим так же, как схему: подстрочно, в загруженном модуле, идемпотентно, и стенд
# `tests/t_tool_text_mac.py` краснеет на каждую пару, которой в дереве больше нет.

#: Указатель руки `computer`: русская строка дерева и её английская проекция.
MAC_POINTER_TEXT: tuple[tuple[str, str], ...] = (
    ("Windows-компьютер Егора", "компьютер владельца (macOS)"),
    ("Yegor's Windows computer", "the owner's Mac"),
    ("PowerShell", "zsh"),
)

#: Блок владельца в кадре (`contract.owner_tools`). Только про тело и его оболочку:
#: остальное в блоке — про сервер, и на Mac оно ровно так же неверно, как на Windows.
MAC_OWNER_TEXT: tuple[tuple[str, str], ...] = (
    ("The Windows PC is your DIRECT body", "This Mac is your DIRECT body"),
    ("run/poll/stop PowerShell, observe files and screen",
     "run/poll/stop shell (zsh) processes, observe files and screen"),
    ("spawning coding_agent subagents on Windows still goes through it. The PC has no LLM",
     "spawning coding_agent subagents there still goes through it. The Mac has no LLM"),
)

#: Имя отрезка кадра с блоком владельца — как его метит дерево (`frame_trace.mark`).
OWNER_TOOLS_MARK = "contract.owner_tools"


def mac_pointer_text(text: str) -> str:
    """Строка указателя руки `computer` словами macOS. Чистая, идемпотентна."""
    for old, new in MAC_POINTER_TEXT:
        text = text.replace(old, new)
    return text


def mac_owner_text(text: str) -> str:
    """Блок владельца словами macOS. Чистая, идемпотентна."""
    for old, new in MAC_OWNER_TEXT:
        text = text.replace(old, new)
    return text


def pointers_for_mac(agent_mod) -> int:
    """Указатель руки `computer` — в обоих словарях дерева. -> сколько строк тронуто.

    `HAND_PURPOSE` — русский оригинал у самого дерева; `tool_text_en.POINTER_PURPOSE` —
    английская проекция, которую модель читает при включённом рычаге. Правится то,
    что есть; чужая форма словаря не роняет.
    """
    touched = 0
    ru = getattr(agent_mod, "HAND_PURPOSE", None)
    en_mod = getattr(agent_mod, "tool_text_en", None)
    en = getattr(en_mod, "POINTER_PURPOSE", None)
    for table in (ru, en):
        if not isinstance(table, dict):
            continue
        old = table.get("computer")
        if isinstance(old, str):
            new = mac_pointer_text(old)
            if new != old:
                table["computer"] = new
                touched += 1
    return touched


def owner_words_for_mac(agent_mod) -> bool:
    """Блок владельца в кадре — словами macOS. -> обёртка поставлена этим вызовом.

    Текст блока — литерал внутри сборки промпта, снаружи его не поправить. Но
    каждый отрезок кадра дерево проводит через `frame_trace.mark(name, zone, kind,
    text)`, и у этого отрезка имя известно (`OWNER_TOOLS_MARK`). Обёртка меняет
    ТОЛЬКО его; всё остальное уходит в `mark` как пришло, тем же объектом — как
    `mark` и обещает. Повторный вызов обёртку не удваивает.
    """
    ft = getattr(agent_mod, "frame_trace", None)
    mark = getattr(ft, "mark", None)
    if not callable(mark):
        return False
    # Обёртка могла уже встать ниже по цепочке — поверх неё ставит свою owner_words.
    probe, hops = mark, 0
    while callable(probe) and hops < 16:
        if getattr(probe, "_helene_mac", False):
            return False
        probe, hops = getattr(probe, "__wrapped__", None), hops + 1

    def mac_mark(name, zone, kind, text, *args, **kwargs):
        if name == OWNER_TOOLS_MARK and isinstance(text, str):
            text = mac_owner_text(text)
        return mark(name, zone, kind, text, *args, **kwargs)

    mac_mark._helene_mac = True
    mac_mark.__wrapped__ = mark
    mac_mark.__name__ = getattr(mark, "__name__", "mark")
    mac_mark.__doc__ = getattr(mark, "__doc__", "")
    ft.mark = mac_mark
    return True


def speak_mac(agent_mod) -> dict:
    """Всё, что модель читает про тело на Mac, — словами Mac: указатель и блок владельца.

    Схемы тулов правит `describe_for_mac`; здесь — остальные два места. Зовётся из
    `install` только на darwin; на Windows ничего этого нет по построению.
    """
    done = {"pointers": pointers_for_mac(agent_mod), "owner": owner_words_for_mac(agent_mod)}
    log.info("тело: указатель руки и блок владельца — словами macOS (%s)", done)
    return done
