# -*- coding: utf-8 -*-
"""Песочница v1 — ограда для рук, которые трогают машину.

Что даёт честно:
  * рука `shell` выполняется процессом в AppContainer Windows. Ему выдано
    ПОИМЁННО: читать — рантайм, код продукта и код дерева; читать и писать — дом
    агента (`data/memory`, `data/soul`, `data/workspace`). Секреты владельца
    (`helene.json`, `data/memory/llm.json`, `data/memory/.state/anatomy.json`,
    `data/relay`, `data/telegram`) закрыты — снятым наследованием и поимённой
    выдачей. Сеть — по ручке `sandbox.network` (по умолчанию есть);
  * дерево процессов команды живёт в job-объекте: таймаут убивает и внуков, а
    число живых процессов ограничено;
  * файловые руки: fs_read/fs_ls/fs_search — в пределах папки Hélène,
    fs_write/fs_edit — только в дереве данных (папка программы, включая
    `helene-svc.exe` под LocalSystem, для них только для чтения). Секреты
    закрыты и здесь;
  * монтирование: папка владельца, названная в `sandbox.mounts`, открывается
    агенту сверх дома — на чтение или на чтение и запись, — и видна ему как
    обычная папка внутри дома (`workspace/mnt/<имя>`, стык junction'ом). Просьбу
    о папке агент подаёт рукой `mount_request`; решение принимает владелец.
Чего не даёт: остальные руки (computer, host_ctl, run, coding_*) не огорожены.
До 10.09 эта строка была ЕДИНСТВЕННЫМ местом, где о них говорилось, — то есть
комментарием для того, кто читает исходник, а владельцу продукт показывал
«песочница: shell в контейнере» и молчал. Теперь снимок устройства везёт отчёт
поимённо (`hands_report`, карта `MACHINE_HANDS` ниже), и окно рисует его двумя
списками: что накрыто и что нет. Умолчание отчёта — fail-closed: рука, о которой
ограда не знает, считается вне её. Это ограда, не тюрьма для самого агента: его
память и код внутри папки Hélène ему доступны.

⚠ ОКНА. Ограда НЕ накрывает руку окон — в контейнер уходит только `bash -lc`
(шим `_SubprocessShim.run`), а обёртка пути стоит на пяти файловых руках.
`computer` в этих списках нет и не должно быть: тело, которое водит окнами
(`helene-body.exe` + мост `helene-bridge.exe`, порт UIA сделан 06.09), живёт
СНАРУЖИ контейнера, в сессии владельца — у AppContainer нет доступа ни к чужим
окнам, ни к рабочему столу. Поднимает тело раннер (`body.py`), а включает или
выключает — владелец, опцией «Управление компьютером» с четырьмя правами; от
режима опция не зависит. Строку про окна в анатомию даёт `body.windows_truth()`,
здесь копии нет.

Включается `sandbox.enabled` в helene.json (по умолчанию включена). Если контейнер
поднять не удалось (старая Windows, ошибка прав), shell работает без ограды,
и причина видна в анатомии и в логе — молча притворяться защитой нельзя. То же
на КАЖДОМ вызове: сорвавшийся запуск в контейнере правит STATE, а не оставляет в
анатомии слово «AppContainer» над незащищённой командой.

Обратная операция — `revoke()` (и `python fence.py --revoke <папка>`): снять ACE
и удалить профиль контейнера. Её зовёт снятие продукта; без неё профили и права
копились бы вечно, по одному на каждую папку установки.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import locale
import logging
import os
import stat as _stat
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("helene.fence")

# Типы Windows API. Модуль обязан ИМПОРТИРОВАТЬСЯ и на POSIX: его читают
# стенды, снимок устройства и — с 0.5.3 — порт на Linux, где ограду ставит не
# AppContainer, а bubblewrap. Структуры ниже описаны на уровне модуля, и
# прятать каждую под `if os.name == "nt"` значило бы разнести один класс по
# двум веткам; вместо этого на POSIX подставляются те же ctypes-типы. Собранные
# структуры там никогда не используются: до них доходит только код, который сам
# стоит под проверкой платформы.
if os.name == "nt":
    import ctypes.wintypes as wt  # noqa: E402 — только на Windows
else:
    class _WinTypesOnPosix:
        """Имена wintypes теми же ctypes-типами — чтобы структуры описались."""

        BYTE = ctypes.c_ubyte
        WORD = ctypes.c_uint16
        DWORD = ctypes.c_uint32
        BOOL = ctypes.c_int
        HANDLE = ctypes.c_void_p
        LPVOID = ctypes.c_void_p
        LPWSTR = ctypes.c_wchar_p
        LPCWSTR = ctypes.c_wchar_p
        ULONG = ctypes.c_ulong
        LARGE_INTEGER = ctypes.c_longlong

    wt = _WinTypesOnPosix()

#: Строка про окна до того, как спросили тело. Живую даёт `body.windows_truth()`
#: (см. шапку модуля): ограда руку окон не трогает, тело живёт снаружи неё.
WINDOWS_UNKNOWN = ("окна: ограда до них не достаёт (в контейнере живёт только "
                   "shell); что с телом — спросить не у кого, модуль body не загрузился")


def windows_truth() -> str:
    try:
        import body as _body
        return _body.windows_truth()
    except Exception:
        return WINDOWS_UNKNOWN


STATE: dict = {"enabled": False, "container": False, "reason": "выключена", "roots": [],
               "writable_roots": [], "mounts": [], "denied_mounts": [],
               "mount_requests": [], "windows": WINDOWS_UNKNOWN}


# --------------------------------------------------------------------------- #
#  Кодировка вывода shell: русская Windows отвечает НЕ в UTF-8
# --------------------------------------------------------------------------- #

def _oem_codepage() -> str:
    """Кодовая страница консоли (родные утилиты пишут в неё): cp866 на русской."""
    try:
        return "cp" + str(ctypes.windll.kernel32.GetOEMCP())
    except Exception:
        return "cp866"


def _ansi_codepage() -> str:
    """ANSI-страница системы (в неё пишет свой текст busybox): cp1251 на русской."""
    try:
        return "cp" + str(ctypes.windll.kernel32.GetACP())
    except Exception:
        return locale.getpreferredencoding(False) or "cp1251"


def decode_output(raw: bytes) -> str:
    """Байты вывода команды -> текст. UTF-8, иначе кодовая страница системы.

    ⚠ НАЙДЕНО ЖИВЬЁМ. `runtime/bash.exe` — busybox-w32 — пишет свой текст в ANSI
    (cp1251 на русской Windows), родные утилиты (ipconfig, dir, sc) — в OEM
    (cp866), а содержимое UTF-8 файлов проходит насквозь. Продукт читал всё это
    как UTF-8:
      * с оградой (errors="replace") агент получал U+FFFD-суп вместо имён своих
        же файлов по-русски;
      * без ограды UnicodeDecodeError летел ВНУТРИ subprocess._readerthread, то
        есть мимо `except Exception` в её tool_shell, и рука возвращала агенту
        «(пустой вывод)» — прибор утверждал, что команда ничего не вывела.
    Декодируем ПОСТРОЧНО: в одном выводе строки бывают от разных производителей
    (`ls` в cp1251 и `ipconfig` в cp866 в одном конвейере), и общая для всего
    вывода кодировка испортила бы половину.
    """
    if not raw:
        return ""
    ansi, oem = _ansi_codepage(), _oem_codepage()
    out = []
    for chunk in raw.split(b"\n"):
        try:
            out.append(chunk.decode("utf-8"))          # файлы и сама она — в UTF-8
            continue
        except UnicodeDecodeError:
            pass
        # Однобайтовая страница декодирует ЛЮБЫЕ байты, поэтому «первая, которая
        # не упала» здесь не работает: выбираем по правдоподобию текста.
        best, best_score = "", -1.0
        for enc in (ansi, oem):
            try:
                text = chunk.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
            score = _plausible(text)
            if score > best_score:
                best, best_score = text, score
        out.append(best if best_score >= 0 else chunk.decode("utf-8", "replace"))
    return "\n".join(out)


def _plausible(text: str) -> float:
    """Доля «нормальных» символов: латиница/цифры/знаки и русские буквы.

    Различает две однобайтовые страницы без словарей: cp866-текст, прочитанный
    как cp1251, разваливается в редкие буквы (Ќ, Є) и типографский мусор
    (©, ®, неразрывный пробел) — здесь это видно числом.
    """
    if not text:
        return 1.0
    good = 0
    for ch in text:
        if ch.isascii() or "А" <= ch <= "я" or ch in "Ёё№«»—–°":
            good += 1
    return good / len(text)

_PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_NO_WINDOW = 0x08000000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_CREATE_SUSPENDED = 0x00000004
_STARTF_USESTDHANDLES = 0x00000100
_WAIT_TIMEOUT = 0x102
_INTERNET_CLIENT_SID = "S-1-15-3-1"
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_ACTIVE_PROCESS_LIMIT = 64      # столько процессов хватает всему, кроме бомбы


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wt.DWORD)]


class _SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", ctypes.c_void_p),
        ("Capabilities", ctypes.POINTER(_SID_AND_ATTRIBUTES)),
        ("CapabilityCount", wt.DWORD),
        ("Reserved", wt.DWORD),
    ]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR),
        ("lpTitle", wt.LPWSTR), ("dwX", wt.DWORD), ("dwY", wt.DWORD),
        ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD), ("dwXCountChars", wt.DWORD),
        ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD), ("dwFlags", wt.DWORD),
        ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD), ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE),
    ]


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", _STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE),
                ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


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


def _dlls():
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    userenv.CreateAppContainerProfile.restype = ctypes.c_long
    userenv.CreateAppContainerProfile.argtypes = [
        wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, wt.DWORD,
        ctypes.POINTER(ctypes.c_void_p)]
    userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
    userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [
        wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wt.BOOL
    advapi32.ConvertStringSidToSidW.argtypes = [wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.ConvertStringSidToSidW.restype = wt.BOOL
    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    kernel32.InitializeProcThreadAttributeList.restype = wt.BOOL
    kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p, wt.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.c_void_p, ctypes.c_void_p]
    kernel32.UpdateProcThreadAttribute.restype = wt.BOOL
    kernel32.CreateProcessW.argtypes = [
        wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL, wt.DWORD,
        ctypes.c_void_p, wt.LPCWSTR, ctypes.POINTER(_STARTUPINFOEXW),
        ctypes.POINTER(_PROCESS_INFORMATION)]
    kernel32.CreateProcessW.restype = wt.BOOL
    kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel32.WaitForSingleObject.restype = wt.DWORD
    kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    kernel32.ResumeThread.argtypes = [wt.HANDLE]
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wt.HANDLE
    kernel32.SetInformationJobObject.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p,
                                                 wt.DWORD]
    kernel32.SetInformationJobObject.restype = wt.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wt.BOOL
    kernel32.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
    kernel32.TerminateJobObject.restype = wt.BOOL
    return userenv, kernel32, advapi32


def secret_paths(install_root: Path, tree: Path) -> list[Path]:
    """Секреты владельца — ОДИН список на права контейнера и на файловые руки.

    Списков было два (icacls в `Container._secrets` и проверка рук в `install`),
    и они уже разошлись бы: анатомия закрыта здесь для обоих сразу.

    `memory/.state/anatomy.json` — снимок устройства для окна. Он лежит внутри
    `memory`, которая выдана контейнеру рекурсивно на изменение, и до 04.09 нёс
    в себе блок `model` целиком, то есть ключи владельца: рука shell самой
    модели читала их обычным `cat`. Ключи из файла убраны (`boot.public_model`),
    но дверь закрывается с обеих сторон — снимок устройства это ещё и карта
    того, где что лежит, и контейнеру она не нужна.

    `memory/.state/desk-token` — СЕКРЕТ КАНАЛА: оболочка кладёт его сюда и им же
    в `HELENE_TOKEN` открывает трубе роль владельца. Он тоже лежит в `memory`
    (rw для контейнера), и без него `cat desk-token` + `curl 127.0.0.1:<порт>/
    api/agent-config?key=…` из shell отдавали бы shell роль владельца: ключ
    модели, токен бота, правку конфига и конституции. Секрет на всех платформах
    (Windows читал его AppContainer из `memory` так же) — закрываем здесь для
    всех трёх оград сразу.

    ⚠⚠ ДОБАВЛЕНО 19.09 ПО НАХОДКЕ СУДЕЙ — четыре файла, которых здесь не было:

    `memory/.state/body-token` — КЛЮЧ УСТРОЙСТВА. Под службой движок кладёт его
    сюда, и по нему тело (`helene-body`) пускает к экрану, мыши и клавиатуре
    владельца. Агент под оградой мог прочитать его своей же командой `shell` и
    ходить в тело МИМО тула `computer` — то есть мимо всех его проверок и мимо
    записи в журнал.

    `memory/.state/broker-asks.json` и `broker-answers.json` — ОБМЕН С БРОКЕРОМ.
    Просьбу пишет рука брокера, ответ — оболочка; оба файла лежат в `memory`,
    выданной контейнеру на изменение. Агент с доступом на запись мог переписать
    «зачем» в просьбе, которую читает владелец (то есть подписать одно, а
    выполнить другое), и — хуже — подделать СВОЙ ЖЕ ответ в `broker-answers.json`
    со словом `allowed`: рука прочитала бы согласие, которого владелец не давал.

    `memory/.state/broker-token` — токен трубы службы на Windows. Права на нём
    ставит сама служба («СИСТЕМА, администраторы, владелец»), и в песочнице он
    и так недоступен; здесь он назван явно, чтобы файловые руки (`STATE["denied"]`)
    тоже его не отдавали и чтобы список секретов был ОДИН, а не «часть тут,
    часть в службе».
    """
    state = Path(tree) / "memory" / ".state"
    return [Path(install_root) / "helene.json",
            Path(tree) / "memory" / "llm.json",
            state / "anatomy.json",
            state / "desk-token",
            state / "body-token",
            state / "broker-token",
            state / "broker-asks.json",
            state / "broker-answers.json",
            Path(tree) / "relay",
            Path(tree) / "telegram"]


#: Секреты, которых на машине может не быть вовсе, и это НОРМА, а не «ещё не
#: создан». `body-token` пишется только под службой, `broker-token` — только
#: когда служба установлена, файлы обмена брокера — только когда агент попросил
#: хоть раз. Без этого списка `_secure_secrets` считал бы установку вечно
#: недоделанной, не ставил маркер и гонял `icacls` по кругу на каждом старте.
SECRETS_MAYBE_ABSENT = ("body-token", "broker-token",
                        "broker-asks.json", "broker-answers.json")


def shut_out_container(path: Path, *, share_read: bool = False) -> bool:
    """Закрыть ОДИН файл от контейнера прямо сейчас. -> получилось ли.

    Нужна отдельно от `_secure_secrets`, потому что права ставятся один раз на
    установку (маркер), а этот файл переписывается на каждом старте: запись идёт
    через `os.replace`, и новый файл приходит с правами, унаследованными от
    папки. Сужение, поставленное вчера, на сегодняшнем файле не живёт.

    Способ — тот же единственный, который РАБОТАЕТ против AppContainer (замерено
    дважды): не `/deny`, а снять наследование и выдать файл поимённо.

    `share_read` — для файлов, которые секретом НЕ являются и закрываются от
    контейнера просто потому, что ему там нечего делать (снимок устройства).
    Такому файлу оставляется чтение всем вошедшим в систему (`S-1-5-11`): иначе
    в режиме службы руннер под LocalSystem сузил бы права на себя, и окно,
    работающее под пользователем, перестало бы читать собственную анатомию.
    Контейнеру это ничего не открывает: токену AppContainer мало прав
    пользователя, ему нужна ещё и ACE его пакета или capability — а её здесь
    больше нет (проверено живьём, см. стенд `r2/t_fence_anatomy.py`).
    """
    target = Path(path)
    if os.name != "nt":
        # ⚠ Не «молча False» (судьи 19.09). Вне Windows ACL нет, и защиту даёт
        # не этот вызов, а сам профиль ограды: на macOS seatbelt запрещает эти
        # пути и на чтение, и на запись (`fence_macos.Container.profile`, секция
        # секретов), на Linux они подменяются пустым tmpfs (`fence_posix`).
        # Молчание здесь читалось бы как «защиты нет» — а она есть, просто
        # ставится один раз на профиль, а не по файлу.
        #
        # Один раз за жизнь процесса, а не на каждый вызов: эту функцию зовут на
        # каждую запись анатомии и каждую просьбу о папке, и строка в журнал
        # каждый раз была бы не честностью, а шумом, за которым перестают читать.
        if not STATE.get("said_profile_guards"):
            STATE["said_profile_guards"] = True
            log.info("секреты закрывает ПРОФИЛЬ ограды (seatbelt/bwrap), а не права файлов — "
                     "прав файлов на этой системе мы не меняем")
        return False
    if not target.exists():
        return False
    user = os.environ.get("USERNAME") or ""
    args = [str(target), "/inheritance:r"]
    for who in ([f"{user}:F"] if user else []) + ["*S-1-5-18:F", "*S-1-5-32-544:F"] \
            + (["*S-1-5-11:R"] if share_read else []):
        args += ["/grant:r", who]
    try:
        proc = subprocess.run(["icacls", *args, "/Q"], capture_output=True, text=True,
                              encoding="cp866", errors="replace",
                              creationflags=_CREATE_NO_WINDOW, timeout=60)
    except Exception as exc:
        log.warning("файл не закрыт от песочницы (%s): %s", target, exc)
        return False
    if proc.returncode != 0:
        log.warning("файл не закрыт от песочницы (%s): %s", target,
                    (proc.stdout or proc.stderr or "").strip()[:200])
        return False
    return True


# --------------------------------------------------------------------------- #
#  Монтирование: папки владельца, открытые агенту сверх его дома
# --------------------------------------------------------------------------- #
#
# Правда о том, что смонтировано, лежит в helene.json (`sandbox.mounts`), потому
# что это решение ВЛАДЕЛЬЦА, а не состояние агента: файл правят окно и человек с
# блокнотом, и оба должны видеть один список. Отказы — там же
# (`sandbox.mounts_denied`), чтобы «нет» пережило перезапуск и агент не спрашивал
# по кругу. Просьбы агента живут отдельно, в дереве данных
# (`memory/.state/mounts.json`): это его слово, а не решение владельца, и путать
# их в одном файле нельзя.
#
# ⚠⚠ ЧУЖОЕ МЕСТО, КОТОРОЕ СТИРАЕТ ЭТОТ СПИСОК. `app/src/views/settings.ts` на
# «Сохранить» пишет блок целиком:
#     out.sandbox = { enabled: …, network: … };
# то есть `mounts` и `mounts_denied` исчезают при первом же сохранении настроек.
# Установщик так не делает — он сливает блок ПОЛЕВО (setup/src/install.rs:533).
# Лечится одной строкой в окне: `out.sandbox = { ...(out.sandbox || {}), … }`.
# Сюда список не переносим и тайную копию не заводим: «восстановил папку, которую
# ты убрал» — это ровно та беда, от которой монтирование и защищает.
#
# Три замка, и они разные — снимать надо все три, иначе папка «смонтирована», а
# не открывается:
#   1. гард ДЕРЕВА (`workshop._resolve_read/_resolve_write`) — он запирает руки в
#      доме раньше любой ограды и ничего не знает о монтировании;
#   2. обёртка ограды (`fenced` ниже) — её корни считаются на каждом вызове;
#   3. права AppContainer — контейнеру папка выдаётся поимённо, как и всё
#      остальное (`Container.sync_mounts`).

MOUNT_HOME = "mnt"          # <workspace>/mnt/<имя> — как папка видна агенту
MOUNT_ACCESS = ("read", "write")
_ACCESS_WORDS = {"read": "чтение", "write": "чтение и запись"}
_WRITE_WORDS = {"write", "w", "rw", "readwrite", "read-write", "read_write",
                "modify", "m", "запись", "чтение и запись", "чтение-запись"}
#: Права контейнеру на смонтированное. RX — пройти и прочитать, M — ещё и писать
#: (F не даём никогда: смена владельца и ACL на чужой папке агенту не нужна).
_MOUNT_RIGHTS = {"read": "(OI)(CI)RX", "write": "(OI)(CI)M"}


def mount_access(value) -> str:
    """Слово доступа из конфига -> "read" | "write". Незнакомое = "read".

    Умолчание в сторону чтения намеренно: опечатка в конфиге не должна тихо
    выдавать запись в папку владельца.
    """
    if value is True:
        return "write"
    raw = str(value or "").strip().lower()
    return "write" if raw in _WRITE_WORDS else "read"


def normalize_mount_path(raw) -> Path | None:
    """Строка из конфига -> путь. None — это не абсолютный путь к папке.

    `%USERPROFILE%` и `~` разворачиваем: владелец пишет этот файл руками.

    ⚠ Хвостовой разделитель НЕ срезаем руками: `"C:\\".rstrip("\\/")` даёт `"C:"`,
    а это не корень диска, а «текущая папка на диске C» — путь перестаёт быть
    абсолютным, и проверка «корень диска монтировать нельзя» до него не доходит.
    `Path` сам убирает лишний хвост у обычных путей.
    """
    text = str(raw or "").strip().strip('"')
    if not text:
        return None
    try:
        path = Path(os.path.expandvars(text)).expanduser()
        if not path.is_absolute():
            return None
        return Path(os.path.normpath(str(path)))
    except (OSError, ValueError):
        return None


def _real(path: Path) -> Path:
    """Путь, каким его увидит проверка: с разобранными стыками и ../."""
    try:
        return Path(path).resolve(strict=False)
    except OSError:
        return Path(path)


def _within(child: Path, parent: Path) -> bool:
    """`child` лежит внутри `parent` (или это он сам)."""
    try:
        Path(child).relative_to(Path(parent))
        return True
    except ValueError:
        return False


def _holds(parent: Path, child: Path) -> bool:
    """`parent` СТРОГО выше `child`. Равенство здесь — не «содержит»."""
    return Path(parent) != Path(child) and _within(child, parent)


#: Системные корни macOS и Linux — то же, что windir и Program Files на
#: Windows: смонтировать их значит отдать агенту машину. Дома пользователей
#: сюда не входят: папка внутри `/Users/<имя>` — обычный случай монтирования.
#: ⚠ Не шире: `/private`, `/var` и `/tmp` целиком сюда нельзя — там лежат
#: временные папки пользователя (`/private/var/folders`, `/tmp`), которые
#: монтируют по делу, и стенды кладут туда свои песочницы.
POSIX_SYSTEM_ROOTS = ("/System", "/Library", "/Applications", "/usr", "/bin", "/sbin",
                      "/lib", "/lib64", "/etc", "/private/etc", "/private/var/db",
                      "/dev", "/proc", "/sys", "/boot")


def _system_roots() -> list[Path]:
    if os.name != "nt":
        return [Path(p) for p in POSIX_SYSTEM_ROOTS]
    out = []
    for key in ("windir", "SystemRoot", "ProgramFiles", "ProgramFiles(x86)",
                "ProgramW6432"):
        value = os.environ.get(key)
        if value:
            out.append(_real(Path(value)))
    return out


def mount_refusal(path: Path, install_root: Path, tree: Path) -> str:
    """"" — папку можно смонтировать; иначе причина человеческими словами.

    Проверка одна на всех: и на список из конфига, и на просьбу агента. Иначе
    агент получал бы «записал просьбу» на папку, которую владелец всё равно не
    сможет смонтировать.
    """
    if path is None:
        return ("нужен полный путь к папке, например "
                + ("C:\\Users\\Имя\\Документы" if os.name == "nt" else "/Users/имя/Documents"))
    real = _real(path)
    if real.parent == real:
        return ("корень диска — это снятая ограда, а не монтирование. Выбери "
                "папку внутри него")
    for sysroot in _system_roots():
        if real == sysroot or _within(real, sysroot):
            return ("системная папка" + (" Windows" if os.name == "nt" else "")
                    + ": смонтировать её значит отдать агенту машину. Для такой "
                    "работы есть интерактивный режим")
    root, data = _real(install_root), _real(tree)
    # Порядок: сначала «папка ДЕРЖИТ Hélène» (и сама папка установки), потом
    # «папка ВНУТРИ Hélène». Равенство считается только в свою сторону, иначе
    # дерево данных попадало бы в первую ветку и получало не тот ответ.
    if real == root or _holds(real, root) or _holds(real, data):
        return ("внутри этой папки лежит сама Hélène — её код и ключи владельца. "
                "Смонтировать её значит снять ограду целиком")
    if _within(real, root):
        return "это внутри папки Hélène — там его дом, монтировать нечего"
    if not real.exists():
        return "папки нет — проверь путь"
    if not real.is_dir():
        return "это файл, а монтируются папки"
    return ""


def parse_mounts(cfg: dict, install_root: Path, tree: Path) -> list[dict]:
    """`sandbox.mounts` -> список записей. Плохие НЕ выбрасываются, а называются.

    Каждая запись: path (как написано), real (как проверяется), access, why, at,
    error (пусто = папка открыта). Молча пропущенная строка конфига — это
    владелец, который смонтировал папку и не понимает, почему агент её не видит.

    Формы, которые принимаем (файл правят руками):
        "C:\\Папка"                                   — чтение
        {"path": "…", "access": "write"}              — канон
        {"path": "…", "write": true}                  — тоже понятно
    """
    block = cfg.get("sandbox") if isinstance(cfg, dict) else None
    raw = (block or {}).get("mounts") if isinstance(block, dict) else None
    rows: list[dict] = []
    seen: set[str] = set()
    for item in (raw if isinstance(raw, (list, tuple)) else []):
        if isinstance(item, str):
            item = {"path": item}
        if not isinstance(item, dict):
            continue
        access = mount_access(item.get("access", item.get("write", "read")))
        path = normalize_mount_path(item.get("path"))
        row = {
            "path": str(path) if path else str(item.get("path") or "").strip(),
            "real": str(_real(path)) if path else "",
            "access": access,
            "access_text": _ACCESS_WORDS[access],
            "why": str(item.get("why") or "").strip(),
            "at": str(item.get("at") or "").strip(),
            "error": mount_refusal(path, install_root, tree),
        }
        key = os.path.normcase(row["real"] or row["path"])
        if key in seen:
            # Две строки на одну папку — не ошибка владельца, а следствие того,
            # что просьбу подтверждали дважды. Побеждает первая, вторая молчит.
            continue
        seen.add(key)
        rows.append(row)
    return rows


def parse_denied(cfg: dict) -> list[dict]:
    """`sandbox.mounts_denied` -> отказы владельца. Их читает и агент, и окно."""
    block = cfg.get("sandbox") if isinstance(cfg, dict) else None
    raw = (block or {}).get("mounts_denied") if isinstance(block, dict) else None
    rows: list[dict] = []
    for item in (raw if isinstance(raw, (list, tuple)) else []):
        if isinstance(item, str):
            item = {"path": item}
        if not isinstance(item, dict):
            continue
        path = normalize_mount_path(item.get("path"))
        if path is None:
            continue
        rows.append({"path": str(path), "real": str(_real(path)),
                     "why": str(item.get("why") or "").strip(),
                     "at": str(item.get("at") or "").strip()})
    return rows


def mount_link_name(path: Path, taken: set[str]) -> str:
    """Имя стыка в `mnt/`: имя папки, а при совпадении — с хвостом от пути.

    Хвост считается от полного пути, а не «_2»: два разных `Документы` должны
    оставаться различимыми между перезапусками, иначе агент запомнит путь,
    который завтра поедет на другую папку.
    """
    base = "".join(ch for ch in Path(path).name if ch not in '<>:"/\\|?*').strip(". ")
    base = base or "mount"
    if os.path.normcase(base) not in taken:
        return base
    digest = hashlib.sha1(str(path).lower().encode("utf-8")).hexdigest()[:6]
    return f"{base}-{digest}"


def mount_link_args(link: Path, target: Path) -> list[str]:
    """Команда стыка. Junction (`/J`), а не symlink: прав администратора не просит.

    Symlink на папку требует либо админа, либо режима разработчика — то есть на
    машине обычного владельца монтирование бы молча не работало.
    """
    return ["cmd", "/c", "mklink", "/J", str(link), str(target)]


def mount_grant_args(sid: str, path: Path, access: str) -> list[str]:
    """icacls-аргументы выдачи смонтированной папки контейнеру.

    Без `/T` намеренно: наследование (OI)(CI) на существующие файлы разносит сама
    система, а обход дерева на папке владельца — это минуты и повод для отказа
    на полпути.
    """
    right = _MOUNT_RIGHTS.get(access, _MOUNT_RIGHTS["read"])
    return [str(path), "/grant", f"*{sid}:{right}"]


def mount_revoke_args(sid: str, path: Path) -> list[str]:
    """icacls-аргументы снятия: размонтировали — прав быть не должно."""
    return [str(path), "/remove:g", f"*{sid}", "/remove:d", f"*{sid}"]


def _read_config(path: Path) -> dict:
    """helene.json так, как его сохранил редактор владельца. {} — не прочитан."""
    try:
        try:
            import modes as _modes
            text = _modes.read_config_text(Path(path))
        except Exception:
            raw = Path(path).read_bytes()
            if raw[:3] == b"\xef\xbb\xbf":
                text = raw[3:].decode("utf-8")
            elif raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
                text = raw.decode("utf-16")
            else:
                text = raw.decode("utf-8")
        cfg = json.loads(text)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError) as exc:
        log.warning("монтирование: helene.json не прочитан (%s): %s", path, exc)
        return {}


def _drop_stick(path: Path) -> None:
    """Снять стык. На Windows это junction — `os.rmdir`; на POSIX стык это
    символическая ссылка, и `os.rmdir` на неё падает ENOTDIR, снимать надо
    `os.unlink`. Без этого размонтированная владельцем папка оставалась бы в доме
    навсегда: код снятия молча спотыкался на каждом стыке."""
    if os.name != "nt" and os.path.islink(str(path)):
        os.unlink(str(path))
    else:
        os.rmdir(str(path))


def _is_junction(path: Path) -> bool:
    if os.name != "nt":
        # На POSIX роль стыка играет символическая ссылка: снимать и сверять
        # надо её, иначе размонтированная папка осталась бы в доме навсегда.
        return path.is_symlink()
    checker = getattr(os.path, "isjunction", None)
    if checker is not None:
        try:
            return bool(checker(str(path)))
        except OSError:
            return False
    try:                                        # запасной путь для старых рантаймов
        tag = getattr(os.lstat(str(path)), "st_reparse_tag", 0)
    except OSError:
        return False
    return tag == getattr(_stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)


class Mounts:
    """Смонтированные папки: правда в helene.json, перечитывается по mtime.

    ⚠ Перечитывается, а не читается один раз на старте: владелец подтверждает
    просьбу агента ПОСРЕДИ работы, и «перезапусти продукт, чтобы папка
    появилась» — не ответ. Цена — один `os.stat` на вызов файловой руки.
    """

    def __init__(self, config_path: Path, install_root: Path, tree: Path,
                 workspace: Path, *, links: bool = True):
        self.config_path = Path(config_path)
        self.install_root = Path(install_root)
        self.tree = Path(tree)
        self.workspace = Path(workspace)
        # `links=False` — не делать стыков в `mnt/`: папки всё равно открыты по
        # полному пути. Нужно стенду (проверять разбор списка, не трогая ФС) и
        # годится как запасной путь, если junction'ы на этой ФС не заводятся.
        self.links = bool(links)
        self.container: "Container | None" = None
        # Руннер многопоточный (окно, бот, будильники): перечитывание списка и
        # сведение стыков должны идти по одному, иначе два потока полезут
        # заводить один и тот же junction и один из них честно напишет в журнал
        # «имя занято».
        self._lock = threading.Lock()
        self._loaded = False
        self._stamp = None
        self._rows: list[dict] = []
        self._denied: list[dict] = []

    # --- чтение -------------------------------------------------------------- #

    def _stamp_now(self):
        try:
            st = self.config_path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def refresh(self, *, force: bool = False) -> None:
        stamp = self._stamp_now()
        # `_loaded`, а не «stamp не None»: пропавший конфиг иначе перечитывался бы
        # на каждом вызове руки — по разу на каждую проверку пути.
        if not force and self._loaded and stamp == self._stamp:
            return
        with self._lock:
            if not force and self._loaded and stamp == self._stamp:
                return          # успел другой поток, пока мы ждали замок
            self._loaded = True
            self._stamp = stamp
            cfg = _read_config(self.config_path)
            rows = parse_mounts(cfg, self.install_root, self.tree)
            self._denied = parse_denied(cfg)
            before = [(r["real"], r["access"]) for r in self._rows if not r["error"]]
            after = [(r["real"], r["access"]) for r in rows if not r["error"]]
            self._rows = rows
            if force or before != after:
                self._settle()
            self._publish()

    def rows(self) -> list[dict]:
        self.refresh()
        return list(self._rows)

    def denied(self) -> list[dict]:
        self.refresh()
        return list(self._denied)

    def roots(self, *, write: bool = False) -> list[Path]:
        """Корни для проверки пути. write=True — только те, куда пускают писать."""
        self.refresh()
        return [Path(r["real"]) for r in self._rows
                if not r["error"] and (r["access"] == "write" or not write)]

    def resolve(self, raw, *, write: bool = False) -> Path | None:
        """Путь -> разобранный путь, если он внутри смонтированного. Иначе None."""
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            path = Path(text)
            if not path.is_absolute():
                # Дерево считает относительные пути от дома — и стык `mnt/<имя>`
                # лежит именно там, так что относительный путь сюда доходит.
                path = self.tree / path
            real = _real(path)
        except (OSError, ValueError):
            return None
        for root in self.roots(write=write):
            if real == root or _within(real, root):
                return real
        return None

    # --- стыки и права ------------------------------------------------------- #

    def _settle(self) -> None:
        """Свести стыки в `mnt/` и права контейнера со списком из конфига."""
        try:
            if self.links:
                self._settle_links()
        except Exception:
            log.exception("монтирование: стыки не сведены (папки всё равно "
                          "открыты по полному пути)")
        try:
            if self.container is not None:
                self.container.sync_mounts(self._rows)
                # macOS: проба профиля С папками могла снять кривое монтирование
                # (см. `fence_macos.Container.sync_mounts`) — покажем это окну.
                STATE["mounts_fault"] = getattr(self.container, "mounts_fault", "")
        except Exception:
            log.exception("монтирование: права контейнеру не выданы")

    def _settle_links(self) -> None:
        home = self.workspace / MOUNT_HOME
        live = [r for r in self._rows if not r["error"]]
        if not live and not home.exists():
            return
        home.mkdir(parents=True, exist_ok=True)
        taken: set[str] = set()
        wanted: dict[str, str] = {}
        rows_by_name: dict[str, dict] = {}
        for row in live:
            name = mount_link_name(Path(row["path"]), taken)
            taken.add(os.path.normcase(name))
            wanted[name] = row["real"]
            rows_by_name[name] = row
        # Лишние стыки снимаем: размонтировали в окне — папка должна пропасть и
        # из дома. os.rmdir на junction снимает САМ СТЫК, содержимое цели цело.
        try:
            existing = list(home.iterdir())
        except OSError:
            existing = []
        for entry in existing:
            if entry.name in wanted:
                continue
            if _is_junction(entry):
                try:
                    _drop_stick(entry)
                    log.info("монтирование: стык снят — %s", entry)
                except OSError as exc:
                    log.warning("монтирование: стык не снят (%s): %s", entry, exc)
        for name, target in wanted.items():
            link = home / name
            row = rows_by_name[name]
            if _is_junction(link):
                if os.path.normcase(str(_real(link))) == os.path.normcase(target):
                    row["link"] = str(link)
                    continue
                try:
                    _drop_stick(link)
                except OSError as exc:
                    row["link_error"] = f"старый стык не снят: {exc}"
                    log.warning("монтирование: старый стык не снят (%s): %s", link, exc)
                    continue
            if link.exists():
                row["link_error"] = "имя в mnt/ занято не стыком"
                log.warning("монтирование: %s занято не стыком — папка открыта "
                            "только по полному пути", link)
                continue
            if os.name != "nt":
                # На POSIX стык — символическая ссылка: `mklink` здесь нет, а
                # внутрь ограды папка попадает всё равно связыванием
                # (`fence_posix.Container.argv`), не ссылкой.
                try:
                    os.symlink(target, str(link), target_is_directory=True)
                    row["link"] = str(link)
                    log.info("монтирование: %s -> %s", link, target)
                except OSError as exc:
                    row["link_error"] = str(exc)[:160]
                    log.warning("монтирование: ссылка не создана (%s -> %s): %s",
                                link, target, exc)
                continue
            proc = subprocess.run(mount_link_args(link, Path(target)),
                                  capture_output=True, text=True, encoding="cp866",
                                  errors="replace", creationflags=_CREATE_NO_WINDOW,
                                  timeout=60)
            if proc.returncode != 0:
                # Стык — удобство, а не право: папка всё равно открыта по полному
                # пути, поэтому неудача здесь не гасит монтирование.
                row["link_error"] = (proc.stdout or proc.stderr or "").strip()[:160]
                log.warning("монтирование: стык не создан (%s -> %s): %s", link,
                            target, row["link_error"])
            else:
                row["link"] = str(link)
                log.info("монтирование: %s -> %s", link, target)

    def _publish(self) -> None:
        """Показать монтирование окну и агенту: STATE едет в анатомию целиком."""
        STATE["mounts"] = [dict(row) for row in self._rows]
        STATE["denied_mounts"] = list(self._denied)
        STATE["mount_requests"] = self.load_requests()

    # --- просьбы агента ------------------------------------------------------ #

    def requests_path(self) -> Path:
        return self.tree / "memory" / ".state" / "mounts.json"

    def load_requests(self) -> list[dict]:
        try:
            data = json.loads(self.requests_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        rows = data.get("requests") if isinstance(data, dict) else None
        return [r for r in (rows or []) if isinstance(r, dict)]

    def save_requests(self, rows: list[dict]) -> None:
        path = self.requests_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(".tmp-" + path.name)
            tmp.write_text(json.dumps({"v": 1, "updated_at": _stamp_text(),
                                       "requests": rows},
                                      ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("монтирование: просьба не записана (%s): %s", path, exc)
            return
        # ⚠ Файл лежит в `memory`, а она выдана контейнеру на изменение: без этого
        # шага агент мог бы своей же командой `shell` переписать «зачем» в
        # просьбе, которую читает владелец. Права ставим ПОСЛЕ записи — она идёт
        # через os.replace, и новый файл приходит с правами папки.
        if STATE.get("container"):
            shut_out_container(path, share_read=True)

    def answer_for(self, real: str) -> str:
        """Уже сказанное владельцем про этот путь. "" — он ещё не отвечал."""
        key = os.path.normcase(real)
        for row in self._rows:
            if os.path.normcase(row["real"]) == key and not row["error"]:
                return (f"папка уже открыта на {_ACCESS_WORDS[row['access']]} — "
                        f"работай, спрашивать не нужно")
        for row in self._denied:
            if os.path.normcase(row["real"]) == key:
                when = f" ({row['at']})" if row["at"] else ""
                why = f": {row['why']}" if row["why"] else ""
                return (f"владелец отказал{when}{why}. Второй раз не спрашивай — "
                        f"ответ не изменится от повтора")
        return ""

    def ask(self, path_raw: str, why: str, access: str) -> str:
        """Просьба агента о папке. -> строка, которую он увидит вместо ответа."""
        self.refresh()
        why = " ".join(str(why or "").split())
        if len(why) < 3:
            return ("монтирование: скажи ЗАЧЕМ папка — эту строку читает владелец, "
                    "и по ней он решает")
        path = normalize_mount_path(path_raw)
        if path is None:
            return ("монтирование: нужен полный путь к папке, "
                    "например C:\\Users\\Имя\\Документы")
        real = str(_real(path))
        said = self.answer_for(real)
        if said:
            return f"монтирование: {said}"
        refusal = mount_refusal(path, self.install_root, self.tree)
        if refusal:
            return (f"монтирование: {refusal}. Просьбу не записал — владелец не "
                    f"сможет её подтвердить")
        access = mount_access(access)
        rows = self.load_requests()
        key = os.path.normcase(real)
        for row in rows:
            if os.path.normcase(str(row.get("real") or "")) == key:
                row["asked"] = int(row.get("asked") or 1) + 1
                row["why"] = why or str(row.get("why") or "")
                row["access"] = access
                self.save_requests(rows)
                return (f"монтирование: просьба уже ждёт владельца с "
                        f"{row.get('at') or 'прошлого раза'} — повтор ничего не "
                        f"ускоряет, скажи ему словами, если это срочно")
        rows.append({"path": str(path), "real": real, "access": access, "why": why,
                     "at": _stamp_text(), "asked": 1})
        self.save_requests(rows)
        STATE["mount_requests"] = rows
        log.info("монтирование: просьба агента — %s (%s): %s", path,
                 _ACCESS_WORDS[access], why)
        return (f"монтирование: записал просьбу о {path} ({_ACCESS_WORDS[access]}). "
                f"Решает владелец — скажи ему об этом и словами; открытой папка "
                f"станет сама, без перезапуска")

    def forget_answered(self) -> None:
        """Убрать просьбы, на которые владелец уже ответил (открыл или отказал)."""
        rows = self.load_requests()
        left = [r for r in rows if not self.answer_for(str(r.get("real") or ""))]
        if len(left) != len(rows):
            self.save_requests(left)
            STATE["mount_requests"] = left

    def describe(self) -> str:
        """Одна строка о монтировании для журнала и анатомии."""
        rows = [r for r in self.rows() if not r["error"]]
        bad = [r for r in self._rows if r["error"]]
        if not rows and not bad:
            return "смонтированных папок нет"
        parts = [f"{r['path']} ({_ACCESS_WORDS[r['access']]})" for r in rows]
        tail = f"; не открыты: {len(bad)}" if bad else ""
        return ("смонтировано: " + (", ".join(parts) or "нет")) + tail


def _stamp_text() -> str:
    return time.strftime("%d.%m.%Y %H:%M")


class Container:
    """AppContainer для одной установки: имя от пути папки, права выданы один раз."""

    def __init__(self, install_root: Path, workspace: Path, network: bool):
        self.root = install_root
        self.workspace = workspace
        self.network = network
        self.userenv, self.kernel32, self.advapi32 = _dlls()
        digest = hashlib.sha1(str(install_root).lower().encode("utf-8")).hexdigest()[:12]
        self.name = f"helene.shell.{digest}"
        self.sid = ctypes.c_void_p()
        self.sid_text = ""
        self.caps: list[ctypes.c_void_p] = []

    def prepare(self) -> None:
        hr = self.userenv.CreateAppContainerProfile(
            self.name, "Hélène shell", "Ограда shell-руки агента Hélène", None, 0,
            ctypes.byref(self.sid))
        if hr != 0:
            # 0x800700B7 = уже есть: тогда просто выводим SID из имени.
            hr2 = self.userenv.DeriveAppContainerSidFromAppContainerName(
                self.name, ctypes.byref(self.sid))
            if hr2 != 0:
                raise OSError(f"AppContainer не создался (hr=0x{hr & 0xFFFFFFFF:08X})")
        text = wt.LPWSTR()
        if not self.advapi32.ConvertSidToStringSidW(self.sid, ctypes.byref(text)):
            raise OSError("SID контейнера не читается")
        self.sid_text = str(text.value)
        if self.network:
            cap = ctypes.c_void_p()
            if self.advapi32.ConvertStringSidToSidW(_INTERNET_CLIENT_SID, ctypes.byref(cap)):
                self.caps.append(cap)
        self._grant()

    def _icacls(self, *args: str) -> tuple[int, str]:
        proc = subprocess.run(["icacls", *args, "/Q"], capture_output=True, text=True,
                              encoding="cp866", errors="replace",
                              creationflags=_CREATE_NO_WINDOW, timeout=600)
        return proc.returncode, (proc.stdout or proc.stderr or "").strip()

    def _grant(self) -> None:
        """Права контейнеру — ПОИМЁННО, а не «вся папка Hélène на чтение».

        ⚠ Здесь стояло `(self.root, "(OI)(CI)RX")` — рекурсивное чтение всей папки
        установки. Ограда, чьё объявленное назначение — защитить секреты владельца
        от команды, которую напишет модель, выдавала контейнеру:
          * `helene.json` — ключ модели, токен бота, owner_id открытым текстом;
          * `data/memory/llm.json` — тот же ключ;
          * `data/relay/local_auth/auth.json` — вход в подписку ChatGPT;
          * `data/telegram/account.session` — полный доступ к аккаунту Telegram.
        Сеть у контейнера по умолчанию есть, то есть прочитанное уезжает наружу.
        Теперь читаются только те места, которые агенту действительно нужны — его
        рантайм, код продукта, код дерева, — а его собственные дом и память
        (memory, soul, workspace) ещё и пишутся: описание руки shell обещает ему
        ровно это («душа в /app/soul, память в /app/memory, пиши себе скиллы»), а
        контейнер давал запись только в workspace.

        Отметка версии политики в имени файла: прежняя выданная широкая ACE иначе
        осталась бы навсегда — маркер `acl-*.ok` мешал её пересмотреть.
        """
        # acl3 (06.09): к правам добавился `.git` дерева — личный репозиторий
        # агента; прежний маркер не дал бы выдать его на уже живой установке.
        marker = self.workspace / ".fence" / f"acl3-{self.sid_text}.ok"
        if marker.exists():
            return
        (self.workspace / ".fence").mkdir(parents=True, exist_ok=True)
        (self.workspace / ".tmp").mkdir(parents=True, exist_ok=True)
        tree = self.workspace.parent
        # Прежняя широкая ACE снимается ровно там, где ставилась: наследованные
        # копии на детях уходят вместе с родительской.
        code, said = self._icacls(str(self.root), "/remove:g", f"*{self.sid_text}")
        if code != 0:
            log.warning("прежние права контейнера не сняты: %s", said[:200])
        grants: list[tuple[Path, str]] = [
            (self.root, "RX"),                       # только пройти насквозь
            (tree, "RX"),                            # тоже только пройти
            (self.root / "runtime", "(OI)(CI)RX"),
            (self.root / "app", "(OI)(CI)RX"),
            (self.root / "tree", "(OI)(CI)RX"),
            (tree / "memory", "(OI)(CI)M"),
            (tree / "soul", "(OI)(CI)M"),
            # Личный репозиторий агента (boot.seed_git): без записи сюда её
            # `git commit` из-под ограды падал бы на index.lock. Ключей в нём
            # нет — память и relay/ в .gitignore, а сам корень дерева по-прежнему
            # только «пройти насквозь».
            (tree / ".git", "(OI)(CI)M"),
            (self.workspace, "(OI)(CI)F"),
        ]
        writable = [tree / "memory", tree / "soul", self.workspace]
        for path, right in grants:
            if not path.exists():
                continue
            code, said = self._icacls(str(path), "/grant", f"*{self.sid_text}:{right}")
            if code != 0:
                raise OSError(f"icacls {path}: {said}")
        pending = self._secure_secrets(tree)
        STATE["writable_roots"] = [str(p) for p in writable]
        STATE["denied"] = [str(p) for p in self._secrets(tree)]
        if not pending:
            marker.write_text(time.strftime("%Y-%m-%d %H:%M"), encoding="utf-8")

    def _secure_secrets(self, tree: Path) -> bool:
        """Закрыть секреты владельца от контейнера. -> True, если что-то ещё не создано.

        ⚠ ЗАМЕРЕНО ЖИВЬЁМ, и это не то, чего ждёшь: `icacls /deny *<SID контейнера>`
        НЕ закрывает файл. Контейнер спокойно прочитал llm.json, на котором стояла
        ACE «(N)» — доступ ему даёт другая строка ACL (унаследованное разрешение
        capability-SID S-1-15-3-…, которое в пользовательских папках стоит сплошь).
        Работает единственное: снять НАСЛЕДОВАНИЕ и выдать файл поимённо —
        владельцу, SYSTEM и админам. Проверено обоими способами на живом
        AppContainer: с `/deny` — «прочитал», с `/inheritance:r /grant:r` —
        «Permission denied».

        SYSTEM и админы остаются намеренно: в режиме службы руннер поднимается от
        LocalSystem, и отобрать у него ключ значило бы сломать этот режим.
        """
        user = os.environ.get("USERNAME") or ""
        pending = False
        unprotected: list[str] = []
        for path in self._secrets(tree):
            if not path.exists():
                # Файла может не быть ВООБЩЕ (`SECRETS_MAYBE_ABSENT`): тела под
                # службой нет, служба не ставилась, брокера ни разу не просили.
                # Считать такую установку недоделанной значило бы не ставить
                # маркер никогда и гонять icacls на каждом старте.
                if path.name not in SECRETS_MAYBE_ABSENT:
                    pending = True      # ещё не создан — вернёмся на следующем старте
                continue
            suffix = "(OI)(CI)F" if path.is_dir() else "F"
            args = [str(path), "/inheritance:r"]
            for who in ([f"{user}:{suffix}"] if user else []) + \
                       [f"*S-1-5-18:{suffix}", f"*S-1-5-32-544:{suffix}"]:
                args += ["/grant:r", who]
            code, said = self._icacls(*args)
            if code != 0:
                # Не роняем ограду целиком: без неё shell пошёл бы вообще без
                # ограничений. Но и молчать нельзя — причина едет в анатомию.
                log.error("секрет не закрыт от песочницы (%s): %s", path, said[:200])
                unprotected.append(str(path))
        STATE["unprotected"] = unprotected
        return pending

    def _secrets(self, tree: Path) -> list[Path]:
        """Что контейнеру нельзя читать никогда, даже внутри разрешённых папок."""
        return secret_paths(self.root, tree)

    # --- монтирование -------------------------------------------------------- #

    def _mount_record(self) -> Path:
        """Что уже выдано контейнеру. Без этого файла снятие невозможно.

        ⚠ Маркер `acl2-*.ok` здесь не годится: он про ОДИН неизменный набор прав,
        выданный раз на установку, а список монтирования владелец правит на ходу.
        Снимать права по одному лишь текущему списку тоже нельзя — снятая из
        конфига строка в нём как раз отсутствует.
        """
        return self.workspace / ".fence" / f"mounts-{self.sid_text}.json"

    def sync_mounts(self, rows: list[dict]) -> None:
        """Свести права контейнера на смонтированные папки с их списком.

        Права выдаются только на саму папку: пройти через её родителей токену
        AppContainer позволяет SeChangeNotifyPrivilege (обход проверки прохода),
        которая есть у любого процесса. Тем же приёмом живут гранты `_grant` на
        runtime/app/tree — иначе пришлось бы дырявить весь путь до неё.
        """
        record = self._mount_record()
        try:
            granted = json.loads(record.read_text(encoding="utf-8"))
            granted = granted if isinstance(granted, dict) else {}
        except (OSError, ValueError):
            granted = {}
        before = dict(granted)
        want = {os.path.normcase(r["real"]): r for r in rows if not r["error"]}
        for key, was in list(granted.items()):
            if key in want and want[key]["access"] == was.get("access"):
                continue
            path = Path(was.get("path") or key)
            code, said = self._icacls(*mount_revoke_args(self.sid_text, path))
            if code != 0:
                log.warning("монтирование: права контейнера не сняты с %s: %s",
                            path, said[:200])
            granted.pop(key, None)
        for key, row in want.items():
            if key in granted:
                continue
            path = Path(row["real"])
            code, said = self._icacls(
                *mount_grant_args(self.sid_text, path, row["access"]))
            if code != 0:
                # ⚠ Не `row["error"]`: неудача здесь закрывает папку только для
                # `shell` (он живёт в контейнере). Файловые руки работают правами
                # владельца, и отнимать у них смонтированную папку из-за чужой
                # беды значило бы наказать владельца дважды.
                row["grant_error"] = f"права контейнеру не выданы: {said[:160]}"
                log.error("монтирование: %s — %s (shell её не увидит, файловые "
                          "руки увидят)", path, row["grant_error"])
                continue
            granted[key] = {"path": str(path), "access": row["access"],
                            "at": _stamp_text()}
            log.info("монтирование: контейнеру выдана %s (%s)", path,
                     _ACCESS_WORDS[row["access"]])
        if granted == before:
            return          # ничего не менялось — не трогаем и файл записи
        try:
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text(json.dumps(granted, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        except OSError as exc:
            log.warning("монтирование: список выданного не записан (%s): %s",
                        record, exc)

    def _make_job(self):
        """Задание для дерева процессов команды. None — не вышло (это не повод падать)."""
        try:
            job = self.kernel32.CreateJobObjectW(None, None)
            if not job:
                return None
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = _JOB_ACTIVE_PROCESS_LIMIT
            if not self.kernel32.SetInformationJobObject(
                    job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                    ctypes.byref(info), ctypes.sizeof(info)):
                log.warning("лимит процессов заданию не поставлен (код %d)",
                            ctypes.get_last_error())
            return job
        except Exception as exc:
            log.warning("задание для команды не создалось: %s", exc)
            return None

    def run(self, argv: list[str], cwd: Path, timeout: float) -> tuple[str, int, bool]:
        """Запустить argv в контейнере; -> (вывод, код, прервано по таймауту)."""
        caps_arr = (_SID_AND_ATTRIBUTES * max(1, len(self.caps)))()
        for i, cap in enumerate(self.caps):
            caps_arr[i].Sid = cap
            caps_arr[i].Attributes = 4  # SE_GROUP_ENABLED
        sc = _SECURITY_CAPABILITIES()
        sc.AppContainerSid = self.sid
        sc.Capabilities = ctypes.cast(caps_arr, ctypes.POINTER(_SID_AND_ATTRIBUTES)) if self.caps else None
        sc.CapabilityCount = len(self.caps)
        size = ctypes.c_size_t(0)
        self.kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        attrs = ctypes.create_string_buffer(size.value)
        if not self.kernel32.InitializeProcThreadAttributeList(attrs, 1, 0, ctypes.byref(size)):
            raise OSError("InitializeProcThreadAttributeList")
        if not self.kernel32.UpdateProcThreadAttribute(
                attrs, 0, _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                ctypes.byref(sc), ctypes.sizeof(sc), None, None):
            raise OSError(f"UpdateProcThreadAttribute: {ctypes.get_last_error()}")

        tmp = self.workspace / ".tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        out_path = tmp / f"shell-{os.getpid()}-{int(time.time() * 1000)}.out"
        fd = os.open(str(out_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_BINARY)
        import msvcrt   # только здесь: модуль импортируется и на Linux (сервер), где msvcrt нет
        handle = msvcrt.get_osfhandle(fd)
        os.set_handle_inheritable(handle, True)

        si = _STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(si)
        si.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        si.StartupInfo.hStdOutput = handle
        si.StartupInfo.hStdError = handle
        si.StartupInfo.hStdInput = None
        si.lpAttributeList = ctypes.cast(attrs, ctypes.c_void_p)
        pi = _PROCESS_INFORMATION()

        runtime = self.root / "runtime"
        # Среда: системные переменные наследуем (без них CreateProcess в
        # AppContainer отвечает 203), своё — поверх: дом и временные файлы в
        # workspace, PATH только из рантайма и системных папок.
        keep = ("SystemRoot", "windir", "SystemDrive", "ComSpec", "LOCALAPPDATA", "APPDATA",
                "USERPROFILE", "ALLUSERSPROFILE", "ProgramData", "PUBLIC", "USERNAME",
                "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE")
        env = {k: os.environ[k] for k in keep if k in os.environ}
        env.update({
            # runtime/shims в PATH не было смысла: сборщик кладёт busybox прямо
            # в runtime/bash.exe, а такой папки в поставке нет вовсе.
            # runtime/git/cmd — MinGit поставки: личный репозиторий агента
            # (boot.seed_git) без него был бы виден только руннеру.
            "PATH": os.pathsep.join([str(runtime), str(runtime / "Scripts"),
                                     str(runtime / "git" / "cmd"),
                                     r"C:\Windows\System32", r"C:\Windows"]),
            "TEMP": str(tmp), "TMP": str(tmp), "HOME": str(self.workspace),
            "PYTHONUTF8": "1", "HELENE_SANDBOX": "1",
        })
        # Локаль — из среды продукта (ручка `env` в helene.json), а не вшитая:
        # здесь стояло жёсткое LANG=ru_RU.UTF-8 для агента любого владельца.
        if os.environ.get("LANG"):
            env["LANG"] = os.environ["LANG"]
        env_block = ctypes.create_unicode_buffer(
            "".join(f"{k}={v}\0" for k, v in sorted(env.items(), key=lambda kv: kv[0].upper())) + "\0")
        cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        # ⚠ CREATE_SUSPENDED + job: без задания таймаут обрывал ТОЛЬКО родителя
        # bash, а поднятый им python или сервер продолжал жить, держал
        # унаследованный дескриптор вывода и оставался в AppContainer навсегда
        # (под службой — до перезагрузки). Форк-бомба внутри контейнера не была
        # сдержана ничем. Задание закрывает оба вопроса: на таймауте умирает всё
        # дерево процессов, а число живых процессов ограничено.
        job = self._make_job()
        ok = self.kernel32.CreateProcessW(
            None, cmdline, None, None, True,
            _EXTENDED_STARTUPINFO_PRESENT | _CREATE_NO_WINDOW
            | _CREATE_UNICODE_ENVIRONMENT | _CREATE_SUSPENDED,
            env_block, str(cwd), ctypes.byref(si), ctypes.byref(pi))
        err = ctypes.get_last_error()
        os.close(fd)
        if not ok:
            if job:
                self.kernel32.CloseHandle(job)
            out_path.unlink(missing_ok=True)
            raise OSError(f"CreateProcess в контейнере не удался (код {err})")
        if job and not self.kernel32.AssignProcessToJobObject(job, pi.hProcess):
            log.warning("процесс не привязан к заданию (код %d): внуки переживут "
                        "таймаут", ctypes.get_last_error())
            self.kernel32.CloseHandle(job)
            job = None
        # Резюмируем В ЛЮБОМ СЛУЧАЕ: не возобновить поток значило бы повесить
        # команду навсегда ради ограничения, которое не встало.
        self.kernel32.ResumeThread(pi.hThread)
        timed_out = False
        wait = self.kernel32.WaitForSingleObject(pi.hProcess, int(timeout * 1000))
        if wait == _WAIT_TIMEOUT:
            timed_out = True
            if job:
                self.kernel32.TerminateJobObject(job, 124)
            self.kernel32.TerminateProcess(pi.hProcess, 124)
            self.kernel32.WaitForSingleObject(pi.hProcess, 5000)
        code = wt.DWORD(0)
        self.kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
        self.kernel32.CloseHandle(pi.hThread)
        self.kernel32.CloseHandle(pi.hProcess)
        if job:
            self.kernel32.CloseHandle(job)
        try:
            text = decode_output(out_path.read_bytes())
        except OSError:
            text = ""
        # Внук (python, запущенный из bash) может ещё держать унаследованный
        # вывод: удаляем с короткими повторами, остаток подметает следующий запуск.
        for _ in range(8):
            try:
                out_path.unlink(missing_ok=True)
                break
            except PermissionError:
                time.sleep(0.25)
        self._sweep(tmp)
        return text, int(code.value), timed_out

    @staticmethod
    def _sweep(tmp: Path) -> None:
        cutoff = time.time() - 3600
        for stale in tmp.glob("shell-*.out"):
            try:
                if stale.stat().st_mtime < cutoff:
                    stale.unlink()
            except OSError:
                continue


class _SubprocessShim:
    """Подмена `subprocess` в модуле руки: команда уходит в контейнер, остальное —
    в настоящий subprocess. Так её tool_shell (журнал, точки отката) остаётся её,
    меняется только исполнение.

    ⚠ Шим ставится ВСЕГДА, даже когда контейнер не поднялся и когда ограда
    выключена: он же единственное место, где вывод команды декодируется по-
    человечески. Без него неогороженный путь читал вывод как UTF-8, ронял
    UnicodeDecodeError внутри потока-читателя subprocess (то есть мимо
    `except Exception` в её tool_shell) и возвращал агенту «(пустой вывод)».

    `route_all` — ЧТО именно уводится в контейнер, и это два разных модуля:

      * `agent` (route_all=False) — только тройка `bash -lc …`, то есть рука
        `shell`. Шире здесь нельзя: тем же `subprocess` модуль агента поднимает
        своё хозяйство, и увести его в AppContainer значило бы чинить дыру,
        ломая продукт;
      * `workshop` (route_all=True) — ВСЁ, что она исполняет: `run` (строкой при
        `shell=True`), `run_tests`, `pip_install`, git проектов. У этого модуля
        других запусков нет (проверено поимённо: 314, 329, 646, 672, 676, 704,
        714), и все они — руки агента.

    До 10.09 шим стоял только в `agent`, и это была дыра не в замысле, а в
    арифметике: `workshop` держит СВОЙ `import subprocess`, поэтому подмена в
    чужом пространстве имён до него не доставала никогда.
    """

    def __init__(self, real, container: "Container | None", workspace: Path,
                 install_root: Path, route_all: bool = False):
        self._real = real
        self._container = container
        self._workspace = workspace
        self._root = Path(install_root)
        self._route_all = bool(route_all)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def _plan(self, args, kwargs) -> tuple[list[str], Path] | None:
        """Что уводим в контейнер и с какой рабочей папкой; None — мимо ограды."""
        # ⚠ Было `str(args[0]).lower().rstrip(".exe")` — rstrip снимает НАБОР
        # символов, а не суффикс: "C:/…/bash.exe" превращалось в "C:/…/bash", в
        # список ("bash","sh") не попадало, и команда молча уходила мимо ограды,
        # пока анатомия продолжала рапортовать «shell в AppContainer».
        if (isinstance(args, (list, tuple)) and len(args) == 3 and args[1] == "-lc"):
            if Path(str(args[0])).stem.lower() not in ("bash", "sh"):
                log.warning("shell-вызов незнакомым интерпретатором %s — веду его тем же "
                            "путём, что и bash (снимать ограду молча нельзя)", args[0])
            return self._bash(str(args[2])), self._workspace
        if not self._route_all:
            return None
        # Рабочая папка вызывающего сохраняется: `run` работает в папке проекта
        # мастерской, и увести её в workspace значило бы выполнить команду не там,
        # где агент её задумал. Папка проекта контейнеру выдана на запись — она
        # внутри workspace.
        cwd = Path(kwargs["cwd"]) if kwargs.get("cwd") else self._workspace
        if kwargs.get("shell"):
            # `run` зовёт со строкой: `subprocess.run(cmd, shell=True, …)`.
            return self._shell(args if isinstance(args, str) else " ".join(map(str, args))), cwd
        if isinstance(args, str):
            return [args], cwd
        return [str(a) for a in args], cwd

    def _bash(self, command: str) -> list[str]:
        """Интерпретатор руки `shell` — busybox поставки, как было до ограды."""
        bash = self._root / "runtime" / "bash.exe"
        return [str(bash) if bash.exists() else "bash", "-lc", command]

    def _shell(self, command: str) -> list[str]:
        """Интерпретатор руки `run` — тот же bash, что и у `shell`.

        ⚠ ЧЕЙ ЭТО ВЫБОР. Здесь стоял `cmd.exe /c` — ровно то, что взял бы сам
        `subprocess` при `shell=True` на Windows. Разница была настоящей и
        неудобной: всё дерево агента написано под Linux, рука `shell` говорит на
        bash (busybox поставки), а рука `run` на той же машине — на cmd. Ограда
        10.09 эту разницу нашла и НЕ стала чинить молча: подменить интерпретатор
        под видом починки безопасности значило бы поменять язык, на котором агент
        написал команду, никого не спросив.
        Спросили — владелец ответил 10.09: «на винде же у нас есть bash в
        комплекте». Он и есть: `runtime/bash.exe` (busybox) кладёт сборка, и он
        уже стоит за рукой `shell`. Теперь обе руки говорят на одном языке, и это
        решение владельца, а не побочный эффект `shell=True`.
        """
        if os.name == "nt":
            return self._bash(command)
        return ["/bin/sh", "-c", command]

    def run(self, args, *pargs, **kwargs):
        plan = self._plan(args, kwargs)
        if plan is None:
            return self._real.run(args, *pargs, **kwargs)
        if self._container is None:
            return self._plain(args, *pargs, **kwargs)
        argv, cwd = plan
        timeout = float(kwargs.get("timeout") or 30)
        try:
            out, code, timed_out = self._container.run(argv, cwd, timeout)
        except OSError as exc:
            # Анатомия обязана сказать правду В ТОТ ЖЕ МОМЕНТ: раньше STATE
            # оставался «shell в AppContainer», хотя команда шла без ограды.
            STATE["container"] = False
            STATE["reason"] = f"контейнер не запустил команду: {exc}; shell без ограды"
            log.warning("песочница не запустила команду (%s) — выполняю без ограды", exc)
            return self._plain(args, *pargs, **kwargs)
        if timed_out:
            raise self._real.TimeoutExpired(args, timeout, output=out)
        done = self._real.CompletedProcess(args, code, stdout=out, stderr=None)
        # `check=True` обязан вести себя как у настоящего subprocess: рука,
        # которая ловит CalledProcessError, под оградой не должна получать вместо
        # исключения тихий провал. `workshop` этим не пользуется, но шим стоит не
        # только под ним.
        if kwargs.get("check") and code != 0:
            raise self._real.CalledProcessError(code, args, output=out)
        return done

    def _plain(self, args, *pargs, **kwargs):
        """Без ограды — но через ОДИН декодер вывода, а не через text=True."""
        opts = dict(kwargs)
        for key in ("text", "encoding", "errors", "universal_newlines"):
            opts.pop(key, None)
        if not opts.get("capture_output"):
            opts.setdefault("stdout", self._real.PIPE)
            opts.setdefault("stderr", self._real.STDOUT)
        try:
            done = self._real.run(args, *pargs, **opts)
        except self._real.TimeoutExpired as exc:
            partial = exc.stdout if isinstance(exc.stdout, bytes) else b""
            raise self._real.TimeoutExpired(exc.cmd, exc.timeout,
                                            output=decode_output(partial)) from None
        return self._real.CompletedProcess(
            done.args, done.returncode,
            stdout=decode_output(done.stdout if isinstance(done.stdout, bytes) else b""),
            stderr=(decode_output(done.stderr) if isinstance(done.stderr, bytes)
                    else done.stderr))


def _inside(path: str, roots: list[Path], base: Path) -> bool:
    raw = str(path or "").strip()
    if not raw:
        return True
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = base / p
    try:
        resolved = p.resolve(strict=False)
    except OSError:
        return False
    for root in roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


MOUNT_TOOL = {
    "name": "mount_request",
    "description": (
        "Попросить у владельца папку вне твоего дома. Ты работаешь в папке "
        "Hélène; наружу открыто только то, что владелец смонтировал сам. "
        "action=ask — попросить: path (полный путь к папке), why (зачем она "
        "тебе, одной строкой — это читает владелец), access = read | write. "
        "action=list — что уже открыто, о чём ты уже просил и на что получил "
        "отказ. Просьба не открывает папку: решает владелец, и открытой она "
        "станет без твоего участия. Повторять просьбу бесполезно — отказ "
        "записан, и на второй вопрос придёт он же."),
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["ask", "list"],
                   "description": "ask — попросить папку, list — посмотреть список"},
        "path": {"type": "string", "description": "полный путь к папке"},
        "why": {"type": "string", "description": "зачем она тебе, одной строкой"},
        "access": {"type": "string", "enum": ["read", "write"],
                   "description": "read — только читать, write — читать и писать"},
    }},
}


def _mount_hand(mounts: "Mounts"):
    """Рука `mount_request`: просьба агента о папке и список ответов владельца."""

    def mount_request(action: str = "ask", path: str = "", why: str = "",
                      access: str = "read") -> str:
        try:
            action = str(action or "ask").strip().lower()
            if action == "list" or (action == "ask" and not str(path or "").strip()):
                rows = [r for r in mounts.rows() if not r["error"]]
                denied = mounts.denied()
                asked = mounts.load_requests()
                out = ["Открыто сверх дома: " + (", ".join(
                    f"{r['path']} ({r['access_text']})" for r in rows) or "ничего")]
                if asked:
                    out.append("Ждут ответа владельца: " + ", ".join(
                        f"{r.get('path')} — {r.get('why')}" for r in asked))
                if denied:
                    out.append("Отказано: " + ", ".join(
                        f"{r['path']}" + (f" ({r['why']})" if r["why"] else "")
                        for r in denied))
                if action != "list":
                    out.append("Чтобы попросить папку: mount_request(action=\"ask\", "
                               "path=…, why=…, access=\"read\"|\"write\").")
                return "\n".join(out)
            if action != "ask":
                return "mount_request: action бывает ask или list"
            return mounts.ask(path, why, access)
        except Exception as exc:                      # рука не роняет ход
            log.exception("mount_request упал")
            return f"mount_request: не вышло — {exc}"

    return mount_request


def _open_everything(agent_mod, tree: Path, install_root: Path) -> None:
    """Интерактивный режим: файловые руки видят всё, что доступно учётке владельца.

    Гард ДЕРЕВА (`workshop._resolve_read`/`_resolve_write`) запирает файловые
    руки в доме при любой ограде (см. `_open_home`). В песочнице это правильно,
    и дверь наружу там одна — монтирование. Без ограды это было раздвоение:
    `shell` читал любой файл владельца его правами, а `fs_read` на тот же путь
    отвечал «вне дома», карточка настроек при этом уверяла, что «файлы и так
    открыты его руками». Слово владельца 06.09: «давай открытый доступ в
    интерактивном режиме» — как у Claude Code, не как у Cowork.

    Расширяем решение дерева, не заменяем: внутри дома и кода его отказы
    (секреты, ядро через предложение) остаются как были; снаружи — любой
    абсолютный путь, а дальше решают права учётки, ровно как у `shell`.
    """
    try:
        import workshop
    except Exception:
        log.warning("интерактивный режим: модуль workshop не загрузился — "
                    "файловые руки остаются в доме", exc_info=True)
        return
    read_original = getattr(workshop, "_resolve_read", None)
    write_original = getattr(workshop, "_resolve_write", None)
    if read_original is None or write_original is None:
        log.warning("интерактивный режим: в дереве нет _resolve_read/_resolve_write — "
                    "файловые руки остаются в доме")
        return
    if getattr(read_original, "_helene_open", False):
        return
    homes = [os.path.normcase(str(Path(p).resolve()))
             for p in (tree, install_root / "tree")]

    def _inside_home(path: Path) -> bool:
        text = os.path.normcase(str(path))
        return any(text == h or text.startswith(h + os.sep) for h in homes)

    def _outside(path: str) -> Path | None:
        raw = str(path or "").strip()
        if not raw:
            return None
        p = Path(raw)
        if not p.is_absolute():
            return None          # относительное — это про дом, там решает дерево
        try:
            p = p.resolve()
        except OSError:
            return None
        return None if _inside_home(p) else p

    def _resolve_read(path: str):
        got = read_original(path)
        if got is not None:
            return got
        return _outside(path)

    def _resolve_write(path: str, proposal_id: str = ""):
        got, err = write_original(path, proposal_id)
        if got is not None or proposal_id:
            return got, err
        opened = _outside(path)
        if opened is not None:
            return opened, ""
        return None, err

    _resolve_read._helene_open = True
    _resolve_write._helene_open = True
    workshop._resolve_read = _resolve_read
    workshop._resolve_write = _resolve_write
    log.info("интерактивный режим: файловые руки открыты на всё, что доступно учётке "
             "владельца; дом и код — по правилам дерева")


def _offer_mount_hand(agent_mod, mounts: "Mounts") -> None:
    """Дать агенту руку просьбы — и в список рук модели, и в TOOL_IMPL.

    Дерево править нельзя (это код владельца), поэтому руку заводим тем же
    приёмом, каким руннер подменяет `frame_layout._speaker`: дописываем схему в
    `BASE_TOOLS`, а исполнение — в `TOOL_IMPL`. Без записи в BASE_TOOLS модель об
    этой руке просто не узнает: список рук собирает `offered_tools_for` из
    статических списков дерева.
    """
    impl = getattr(agent_mod, "TOOL_IMPL", None)
    if not isinstance(impl, dict):
        return
    if impl.get(MOUNT_TOOL["name"]) is not None:
        return
    impl[MOUNT_TOOL["name"]] = _mount_hand(mounts)
    tools = getattr(agent_mod, "BASE_TOOLS", None)
    if isinstance(tools, list) and not any(
            isinstance(t, dict) and t.get("name") == MOUNT_TOOL["name"] for t in tools):
        tools.append(dict(MOUNT_TOOL))
    log.info("монтирование: рука mount_request выдана агенту")


def _open_home(agent_mod, mounts: "Mounts") -> None:
    """Пустить файловые руки в смонтированное: гард ДЕРЕВА запирает их в доме.

    ⚠ НАЙДЕНО ПРИ C1, и это не то, чего ждёшь: ограда песочницы — не единственный
    замок. `workshop._resolve_read` отдаёт None всему, что вне BASE/REPO, а
    `_resolve_write` — «путь вне дома», и это происходит ВНУТРИ руки, до всякой
    ограды. Пока эти двое не знают о монтировании, смонтированная папка не
    открывается НИ В КАКОМ режиме — ни в песочнице, ни в интерактивном, ни под
    службой.

    Дерево — код владельца, править его нельзя. Поэтому расширяем его решение
    поверх: сначала спрашиваем оригинал (все его отказы остаются в силе — ядро
    через предложение, секреты, потолки), и только на «вне дома» смотрим список
    монтирования.
    """
    try:
        import workshop
    except Exception:
        log.warning("монтирование: модуль workshop не загрузился — смонтированное "
                    "останется недоступным файловым рукам", exc_info=True)
        return
    read_original = getattr(workshop, "_resolve_read", None)
    write_original = getattr(workshop, "_resolve_write", None)
    if read_original is None or write_original is None:
        log.warning("монтирование: в дереве нет _resolve_read/_resolve_write — "
                    "смонтированное файловым рукам не открыть")
        return
    if getattr(read_original, "_helene_mounts", False):
        return

    def _resolve_read(path: str):
        got = read_original(path)
        if got is not None:
            return got
        return mounts.resolve(path, write=False)

    def _resolve_write(path: str, proposal_id: str = ""):
        got, err = write_original(path, proposal_id)
        if got is not None or proposal_id:
            return got, err
        opened = mounts.resolve(path, write=True)
        if opened is not None:
            return opened, ""
        # Отказ оригинала оставляем дословно, но говорим, что делать дальше:
        # «путь вне дома» без продолжения читается как «сюда нельзя никогда».
        if mounts.resolve(path, write=False) is not None:
            return None, (f"{err}: папка смонтирована только на чтение — "
                          f"попроси запись рукой mount_request")
        return None, err

    _resolve_read._helene_mounts = True
    _resolve_write._helene_mounts = True
    workshop._resolve_read = _resolve_read
    workshop._resolve_write = _resolve_write


def install(agent_mod, tree: Path, cfg: dict, config_path: Path | None = None) -> None:
    """Поднять ограду по helene.json. Ничего не роняет: не вышло — записано почему.

    `config_path` — тот же файл, который читал руннер. Он нужен монтированию:
    список папок перечитывается на ходу, а угадывать имя файла по папке
    установки значило бы завести вторую правду о том, где живёт конфиг.
    """
    sandbox = dict(cfg.get("sandbox") or {})
    enabled = bool(sandbox.get("enabled", True))
    network = bool(sandbox.get("network", True))
    # Ручку `sandbox.enabled` выставляет РЕЖИМ (`modes.apply`, вызывается в
    # руннере до этой строки). Читаем по-прежнему ручку — вторая правда нам не
    # нужна; имя режима берём только чтобы честно назвать причину в анатомии:
    # «выключена в настройках» над интерактивным режимом сбивало с толку.
    mode_name, mode_title = "", ""
    try:
        import modes as _modes
        mode_name = _modes.stated(cfg)[0]
        mode_title = _modes.TITLES.get(mode_name, "")
    except Exception:
        log.debug("режим не спросился — ограда всё равно ставится по ручке",
                  exc_info=True)
    tree = Path(tree).resolve()
    install_root = tree.parent
    workspace = tree / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    STATE.update({"enabled": enabled, "container": False,
                  "roots": [str(install_root)], "writable_roots": [], "denied": [],
                  "mode": mode_name,
                  "reason": ("" if enabled else
                             (f"режим «{mode_title}» — ограда не ставится"
                              if mode_title else "выключена в настройках"))})

    # 1. shell — в контейнер (а шим — всегда, см. его докстринг).
    container = None
    if enabled:
        try:
            if os.name == "nt":
                container = Container(install_root, workspace, network)
                container.prepare()
                STATE["reason"] = (f"shell в AppContainer {container.sid_text}, "
                                   f"сеть {'есть' if network else 'нет'}")
            else:
                # Ограда на POSIX — тот же контракт, другой механизм: на macOS
                # seatbelt (`fence_macos`), на Linux bubblewrap (`fence_posix`).
                # Импорт поздний, чтобы модуль не искали на Windows, где его
                # роль исполняет AppContainer выше.
                if sys.platform == "darwin":
                    import fence_macos as posix_fence  # noqa: PLC0415 — только здесь
                else:
                    import fence_posix as posix_fence  # noqa: PLC0415 — только здесь
                container = posix_fence.Container(
                    install_root, workspace, network, tree=tree,
                    secrets=secret_paths(install_root, tree))
                container.prepare()
                STATE["reason"] = container.describe()
                # Куда пишет shell — в снимок устройства (иначе анатомия говорит
                # «shell пишет никуда»). На Windows это делает `Container._grant`.
                roots = getattr(container, "writable_roots", None)
                if callable(roots):
                    STATE["writable_roots"] = [str(p) for p in roots()]
            STATE["container"] = True
            log.info("песочница: %s", STATE["reason"])
        except Exception as exc:
            container = None
            STATE["reason"] = f"контейнер не поднялся: {exc}; shell без ограды"
            log.warning("песочница: %s", STATE["reason"])
    else:
        log.info("песочница: %s", STATE["reason"])
    if not isinstance(getattr(agent_mod, "subprocess", None), _SubprocessShim):
        agent_mod.subprocess = _SubprocessShim(agent_mod.subprocess, container,
                                               workspace, install_root)
    _shim_workshop(container, workspace, install_root)

    # 2. Гард ДЕРЕВА (`workshop._resolve_read`) запирает файловые руки в доме
    # независимо от нашей ограды, поэтому дом расширяем здесь сами — по-разному
    # для двух оград:
    #   * песочница — монтирование: наружу только то, что владелец назвал, и
    #     рука `mount_request`, чтобы попросить;
    #   * интерактивный — всё, что доступно учётке владельца (слово владельца
    #     06.09). Монтировать тут нечего, и руки просьбы нет: список
    #     `sandbox.mounts` в конфиге остаётся и оживает с песочницей.
    # Раньше монтирование поднималось в любом режиме, и без ограды `fs_read`
    # отвечал «вне дома» там, где `shell` тот же файл спокойно читал.
    mounts = None
    if enabled:
        try:
            mounts = Mounts(Path(config_path) if config_path
                            else install_root / "helene.json",
                            install_root, tree, workspace)
            mounts.container = container
            mounts.refresh(force=True)
            mounts.forget_answered()
            log.info("монтирование: %s", mounts.describe())
            for row in mounts.rows():
                if row["error"]:
                    log.warning("монтирование: %s — %s", row["path"] or "(пустой путь)",
                                row["error"])
        except Exception:
            log.exception("монтирование не поднялось — агент остаётся в доме")
            mounts = None
        if mounts is not None:
            _open_home(agent_mod, mounts)
            _offer_mount_hand(agent_mod, mounts)
    else:
        _open_everything(agent_mod, tree, install_root)
        STATE["reason"] = (STATE["reason"] + "; файловые руки и shell видят всё, "
                           "что доступно учётке владельца, монтирование не нужно")
    # Рука окон (`computer`) — не наша: её подключает `body.install` ПОСЛЕ ограды,
    # тело живёт снаружи контейнера. Здесь только строка правды в анатомию.
    STATE["windows"] = windows_truth()

    if not enabled:
        return

    # 3. файловые руки. Читать — папку продукта, писать — только дерево данных.
    # ⚠ Корнем для ВСЕХ файловых рук была папка установки, то есть fs_write имел
    # право переписать `helene-svc.exe` (его запускает служба под LocalSystem) и
    # `install-service.ps1` (его запускает поднятый через UAC powershell). Это
    # путь к SYSTEM из обычного хода агента, и слово «ограда» в анатомии было
    # неверным. Теперь запись — только там, где живёт он сам.
    impl = getattr(agent_mod, "TOOL_IMPL", None)
    if not isinstance(impl, dict):
        return
    read_roots = [install_root]
    write_roots = [tree]
    secrets = secret_paths(install_root, tree)
    base = Path(getattr(agent_mod, "BASE", install_root))
    # Две границы, и они РАЗНЫЕ — врать одной строкой в анатомии больше нельзя:
    # `writable_roots` — куда пишет shell из контейнера, `hands_write_root` — куда
    # пишут файловые руки.
    STATE["writable_roots"] = STATE.get("writable_roots") or []
    STATE["hands_write_root"] = str(tree)
    STATE["denied"] = [str(p) for p in secrets]

    def _live_roots(write: bool) -> list[Path]:
        """Корни СЕЙЧАС: дом плюс то, что владелец смонтировал минуту назад.

        Считаются на каждом вызове, а не замыкаются на старте: подтверждение
        просьбы в окне обязано действовать без перезапуска продукта.
        """
        roots = list(write_roots if write else read_roots)
        if mounts is not None:
            roots += mounts.roots(write=write)
        return roots

    def _refusal(value: str, roots: list[Path]) -> str:
        return (f"песочница: путь вне разрешённого — {value}. "
                f"Разрешено: {', '.join(str(r) for r in roots)}. "
                "Нужна папка снаружи — попроси её у владельца рукой "
                "`mount_request`, ограду он снимает в настройках.")

    def fenced(name, fn, write: bool):
        def wrapper(*args, **kwargs):
            checked = [kwargs.get(key) for key in
                       ("path", "root", "directory", "glob_root")]
            if args and isinstance(args[0], str):
                checked.append(args[0])
            roots = _live_roots(write)
            for value in checked:
                if not isinstance(value, str) or not value.strip():
                    continue        # пустой путь — не путь; проверять нечего
                if _inside(value, secrets, base):
                    return ("песочница: это секреты владельца (ключ модели, вход в "
                            "подписку, сессия Telegram) — руки туда не ходят.")
                if not _inside(value, roots, base):
                    # Отдельный ответ на «папка смонтирована, но только на
                    # чтение»: общий отказ («путь вне разрешённого») читается
                    # как «её вообще нет», и агент идёт просить то, что у него
                    # уже есть.
                    if write and mounts is not None \
                            and mounts.resolve(value, write=False) is not None:
                        return ("песочница: папка смонтирована только на чтение. "
                                "Нужна запись — попроси её рукой `mount_request` "
                                "с access=\"write\".")
                    return _refusal(value, roots)
            return fn(*args, **kwargs)
        wrapper.__name__ = getattr(fn, "__name__", name)
        wrapper.__doc__ = getattr(fn, "__doc__", "")
        wrapper._helene_fenced = True
        return wrapper

    plan = {"fs_read": False, "fs_ls": False, "fs_search": False,
            "fs_write": True, "fs_edit": True}
    fenced_names = []
    for name, write in plan.items():
        if name in impl and not getattr(impl[name], "_helene_fenced", False):
            impl[name] = fenced(name, impl[name], write)
            fenced_names.append(name)
    log.info("песочница: файловые руки в ограде — %s; запись только в %s%s",
             ", ".join(fenced_names) or "нет", tree,
             "" if mounts is None else " + смонтированное на запись")


def state() -> dict:
    return dict(STATE)


# ─── что ограда накрывает, а что нет ────────────────────────────────────────────
#
# До 10.09 это знание жило ТОЛЬКО в шапке этого файла («остальные руки не
# огорожены»), то есть в комментарии для того, кто читает исходник. Владельцу
# продукт говорил «песочница: shell в контейнере» и молчал о том, что рядом
# стоят руки, исполняющие команды мимо неё. Обещать больше, чем делаешь, —
# ровно то, чего продукт не имеет права.
#
# Карта НЕ решает, огорожена рука или нет: это вычисляется из реальности
# (`_helene_fenced` на обёртке, шим на месте `subprocess` в модуле руки,
# поднялся ли контейнер). Карта отвечает на другой вопрос — КАКИМ БОКОМ рука
# трогает машину, потому что интроспекцией это не выводится: `recall` и
# `run` для питона одинаковые функции.
#
# ⚠ Умолчание — fail-closed. Рука, которой здесь нет, попадает в отчёт как
# «не разобрана» и считается вне ограды, а не «наверное, безопасная». Стенд
# `t_fence_hands.py` держит карту от протухания с другой стороны: имя, которого
# больше нет в живом наборе рук, — это наша ложь о несуществующем.
#: рука → (что трогает, ВСЕ модули, через которые она исполняет).
#: Пусто — рука команд не исполняет, и ограда для неё это проверка пути.
#:
#: ⚠ Модулей у одной руки бывает несколько, и накрытой она считается, только
#: когда огорожены ВСЕ. Это не педантизм: `workshop.run` сперва пробует «пол»
#: (`hands.execute` → бинарь `praxis-hands`), и лишь потом падает в собственный
#: subprocess. В поставке бинаря нет, но у `hands` СВОЙ `import subprocess`, и
#: положи его кто-нибудь в поставку завтра — ограда отказала бы молча, а отчёт
#: продолжал писать «в контейнере».
MACHINE_HANDS: dict[str, tuple[str, tuple[str, ...] | None]] = {
    # Исполняют команды.
    "shell": ("команды", ("agent",)),
    "run": ("команды", ("workshop", "hands")),
    "run_tests": ("команды", ("workshop", "hands")),
    "pip_install": ("команды", ("workshop", "hands")),
    # Forge — семейство из десяти рук, и машину трогают почти все. Исполняют они
    # не сами: `forge.run` поднимает ОТДЕЛЬНЫЙ процесс-надзиратель, и тот уже
    # делает Popen(shell=True). Подмена `subprocess` в этом процессе до него не
    # достаёт — поэтому модуль назван, но ограда его не накрывает.
    #
    # ⚠ И дело даже не в шиме. Forge работает в worktree ЗАДАЧИ, а он лежит там,
    # куда его завёл владелец, — это может быть любой репозиторий на диске, вне
    # папки Hélène. Огородить это нельзя, не сломав сам смысл руки; поэтому она
    # названа, а не спрятана.
    "coding_run": ("команды", ("forge_process",)),
    "coding_process": ("команды", ("forge_process",)),
    "coding_agent": ("команды", ("forge_process",)),
    "coding_verify": ("команды", ("forge_process",)),
    "coding_swarm": ("команды", ("forge_process",)),
    "coding_checkpoint": ("команды", ("forge_process",)),
    "coding_session": ("файлы и команды", ("forge_process",)),
    "coding_edit": ("файлы", None),
    "coding_inspect": ("файлы", None),
    "coding_learn": ("файлы", None),
    # Трогают файлы. Здесь ограда — обёртка пути, и её наличие видно по флагу.
    "fs_read": ("файлы", None),
    "fs_ls": ("файлы", None),
    "fs_search": ("файлы", None),
    "fs_write": ("файлы", None),
    "fs_edit": ("файлы", None),
    # Водят окнами и процессами владельца. Тело живёт СНАРУЖИ контейнера по
    # устройству (у AppContainer нет доступа к чужим окнам), и это не дыра, а
    # граница: права здесь дают галочки владельца, а не ограда.
    "computer": ("окна и файлы владельца", None),
    "host_ctl": ("хост", None),
}

#: Причина, по которой рука вне ограды ПО УСТРОЙСТВУ, а не по недосмотру.
#: Разница читателю важна: по общей строке «не огорожена» нельзя понять, чинится
#: это правкой продукта или не чинится вовсе.
OUTSIDE_BY_DESIGN: dict[str, str] = {
    "computer": "тело живёт снаружи ограды: права дают галочки владельца",
    "host_ctl": "рука хоста ходит к системе мимо контейнера",
}
OUTSIDE_BY_DESIGN.update({
    name: "Forge работает в worktree задачи — он может лежать где угодно на диске"
    for name in ("coding_run", "coding_process", "coding_agent", "coding_verify",
                 "coding_swarm", "coding_checkpoint", "coding_session",
                 "coding_edit", "coding_inspect", "coding_learn")
})

#: Оговорки: рука накрыта НЕ ЦЕЛИКОМ. Приписываются к причине, и молчать о них
#: нельзя — «в ограде» над наполовину огороженной рукой это та же неправда,
#: только мельче.
HAND_CAVEATS: dict[str, str] = {
    # `run_tests(project)` идёт через workshop и в контейнер попадает.
    # `run_tests("self")` — другая дорога: `selfdev` гоняет её тесты в worktree
    # предложения, а `.proposals` контейнеру на запись НЕ выдана (выданы memory,
    # soul, workspace и .git — см. `Container._grant`). Огородить эту ветку
    # значило бы сломать её саморазвитие, поэтому она честно названа.
    "run_tests": ("аргумент \"self\" исполняет selfdev в worktree предложения — "
                  "туда ограда не достаёт"),
}


def hands_report(agent_mod) -> list[dict]:
    """Правда о каждой руке, которая трогает машину: в ограде она или нет.

    Считается из того, что есть на самом деле, а не из списка:
      * `path` — на реализации стоит наша обёртка (`_helene_fenced`);
      * `container` — в модуле руки лежит наш шим И контейнер поднялся;
      * `outside` — ни того, ни другого.
    Имя из `MACHINE_HANDS`, которого в живом наборе нет, приезжает как
    `unknown`: молчать о собственной устаревшей записи нельзя.

    ⚠ Зовётся ТАМ, ГДЕ ЧИТАЮТ, а не на установке ограды, и копии в `STATE` нет.
    Причина конкретная: `body.install` (рука `computer`) и `broker.install`
    выдают свои руки ПОСЛЕ `fence.install` — снимок, снятый внутри установки,
    объявил бы `computer` несуществующей рукой. Второй причины хватило бы и
    одной: ограда меняется на ходу (контейнер может отвалиться на любом
    вызове, см. шим), и отчёт полугодовой свежести — это та же неправда, только
    аккуратно оформленная.
    """
    impl = getattr(agent_mod, "TOOL_IMPL", None)
    if not isinstance(impl, dict):
        return []
    container_up = bool(STATE.get("container"))
    rows: list[dict] = []
    for name in sorted(impl):
        fn = impl[name]
        touches, modules = MACHINE_HANDS.get(name, ("", ()))
        if not touches:
            continue        # рука машину не трогает — ограде о ней сказать нечего
        # Считаем только ЗАГРУЖЕННЫЕ модули: незагруженный ничего не исполняет,
        # и записывать его в дыру значило бы пугать владельца тем, чего нет.
        # Загрузись он позже — отчёт снимается заново на каждой записи снимка и
        # скажет об этом тогда.
        live = [m for m in (modules or ()) if m in sys.modules]
        bare = [m for m in live if not _shim_in(m)]
        if getattr(fn, "_helene_fenced", False):
            fence_kind, why = "path", "проверка пути"
        elif live and not bare:
            if container_up:
                fence_kind, why = "container", f"команда уходит в {container_word()}"
            else:
                fence_kind, why = "outside", STATE.get("reason") or "контейнер не поднялся"
        elif name in OUTSIDE_BY_DESIGN:
            fence_kind, why = "outside", OUTSIDE_BY_DESIGN[name]
        elif bare:
            # Называем ИМЕННО тот модуль, который остался снаружи: по общему
            # «не огорожена» нельзя понять, чинится это ручкой в настройках или
            # правкой продукта.
            fence_kind = "outside"
            why = f"команду исполняет {', '.join(bare)} — шима ограды там нет"
        elif modules:
            fence_kind = "outside"
            why = f"модуль {', '.join(modules)} не загружен — исполнять команду нечем"
        else:
            fence_kind, why = "outside", "ограда до этой руки не достаёт"
        caveat = HAND_CAVEATS.get(name)
        if caveat:
            why = f"{why}; {caveat}"
        rows.append({"name": name, "touches": touches, "fence": fence_kind, "why": why})
    known = set(MACHINE_HANDS)
    try:
        import body as _body_mod
        if not _body_mod.HAS_BODY:
            # Тела в этой сборке нет (прочие POSIX): `body._install_absent` сняла
            # руку `computer` из набора вовсе (как брокер). В отчёте её тоже быть
            # не должно — иначе снятая рука приедет ложным `unknown`.
            known.discard("computer")
    except Exception:
        pass
    unknown = sorted(known - set(impl))
    for name in unknown:
        # Имя, которого в живом наборе рук больше нет. Это наша ложь о
        # несуществующем — говорим о ней вслух, а не вычёркиваем молча.
        rows.append({"name": name, "touches": MACHINE_HANDS[name][0],
                     "fence": "unknown", "why": "руки с таким именем в наборе нет"})
    return rows


def container_word() -> str:
    """Чем огорожен `shell` на этой платформе — одно слово для отчётов.

    На Windows — AppContainer, как и было; на macOS — seatbelt
    (`fence_macos`), на Linux — bubblewrap (`fence_posix`). Слово в отчёте
    обязано совпадать с механизмом: «AppContainer» над seatbelt — та же
    неправда, что «в контейнере» над голой командой, только про другое.
    """
    if os.name == "nt":
        return "AppContainer"
    return "seatbelt" if sys.platform == "darwin" else "bubblewrap"


def _shim_in(module_name: str) -> bool:
    """Стоит ли наш шим на месте `subprocess` в этом модуле — сейчас, не на старте."""
    mod = sys.modules.get(module_name)
    return isinstance(getattr(mod, "subprocess", None), _SubprocessShim)


#: Модули, всё исполнение которых — это руки агента, и потому уходит в контейнер
#: целиком. `hands` здесь ради «пола»: `workshop.run` сперва пробует бинарь
#: `praxis-hands` и только потом свой subprocess.
EXECUTING_MODULES = ("workshop", "hands")


def _shim_workshop(container: "Container | None", workspace: Path, install_root: Path) -> None:
    """Увести исполняющие руки мастерской (`run`, `run_tests`, `pip_install`) в контейнер.

    Отдельный шим, а не тот же объект: у этого `route_all=True` — в контейнер
    уходит ВСЁ, что исполняет модуль, а не одна тройка `bash -lc`. Причина в
    шапке `_SubprocessShim`.

    Модули берём из `sys.modules`, а не импортом: дерево уже загружено (ограда
    ставится после него), а свой импорт поднял бы ВТОРУЮ копию модуля со своим
    `subprocess` — огорожена была бы она, а руки остались бы у первой.
    """
    for name in EXECUTING_MODULES:
        mod = sys.modules.get(name)
        if mod is None:
            log.info("песочница: модуля %s нет — огораживать нечего", name)
            continue
        if isinstance(getattr(mod, "subprocess", None), _SubprocessShim):
            continue
        try:
            mod.subprocess = _SubprocessShim(mod.subprocess, container, workspace,
                                             install_root, route_all=True)
        except Exception:
            log.exception("исполнение модуля %s осталось без ограды", name)
            continue
        log.info("песочница: исполнение %s — %s", name,
                 "в контейнере" if container is not None else "без контейнера (его нет)")


def container_name(install_root: Path) -> str:
    digest = hashlib.sha1(str(Path(install_root).resolve()).lower()
                          .encode("utf-8")).hexdigest()[:12]
    return f"helene.shell.{digest}"


def _granted_mount_paths(install_root: Path, sid_text: str) -> list[Path]:
    """Папки, которым выдавались права этого контейнера. [] — записи нет."""
    record = Path(install_root) / "data" / "workspace" / ".fence" / \
        f"mounts-{sid_text}.json"
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[Path] = []
    for key, row in (data.items() if isinstance(data, dict) else ()):
        raw = (row or {}).get("path") if isinstance(row, dict) else None
        path = normalize_mount_path(raw or key)
        if path is not None:
            out.append(path)
    return out


def revoke(install_root: Path, *, names: list[str] | None = None) -> list[str]:
    """Снять права контейнера и удалить его профиль. -> строки отчёта.

    ⚠ Обратной операции не было НИГДЕ: выключил песочницу — ACE остались; удалил
    продукт — профиль и права остались; переставил в другую папку — завёлся ещё
    один профиль. На машине автора живьём нашлось шесть профилей AppContainer от
    прежних установок и ACE песочницы на файлах давно снятого продукта.

    Смонтированные папки лежат СНАРУЖИ папки установки, и обход `root`/`root/data`
    их не задел бы: их права снимаются по списку выданного
    (`workspace/.fence/mounts-<sid>.json`) — иначе ACE агента остались бы на
    личных папках владельца после снятия продукта.
    """
    root = Path(install_root).resolve()
    report: list[str] = []
    userenv, _kernel32, advapi32 = _dlls()
    userenv.DeleteAppContainerProfile.restype = ctypes.c_long
    userenv.DeleteAppContainerProfile.argtypes = [wt.LPCWSTR]
    for name in (names or [container_name(root)]):
        sid = ctypes.c_void_p()
        if userenv.DeriveAppContainerSidFromAppContainerName(
                name, ctypes.byref(sid)) == 0:
            text = wt.LPWSTR()
            if advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                targets = [root, root / "data"]
                targets += _granted_mount_paths(root, str(text.value))
                for path in targets:
                    if not path.exists():
                        continue
                    proc = subprocess.run(
                        ["icacls", str(path), "/remove:g", f"*{text.value}",
                         "/remove:d", f"*{text.value}", "/T", "/C", "/Q"],
                        capture_output=True, text=True, encoding="cp866",
                        errors="replace", creationflags=_CREATE_NO_WINDOW, timeout=900)
                    report.append(f"права сняты с {path}: код {proc.returncode}")
        hr = userenv.DeleteAppContainerProfile(name)
        report.append(f"профиль {name}: {'удалён' if hr == 0 else f'hr=0x{hr & 0xFFFFFFFF:08X}'}")
    for line in report:
        log.info("снятие песочницы: %s", line)
    return report


if __name__ == "__main__":     # снятие продукта зовёт это же, без импорта харнесса
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--revoke", required=True, help="папка установки Hélène")
    ap.add_argument("--also", nargs="*", default=[],
                    help="имена прежних контейнеров (vera.shell.*, helene.shell.*)")
    parsed = ap.parse_args()
    for row in revoke(Path(parsed.revoke),
                      names=[container_name(Path(parsed.revoke))] + list(parsed.also)):
        print(row)
