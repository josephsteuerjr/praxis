# -*- coding: utf-8 -*-
"""Ограда на macOS: seatbelt (`sandbox-exec`) там, где на Windows стоит AppContainer.

Зачем. Порт на macOS — «основа без тела, службы и брокера», но ограда в основу
входит обязательно (решение владельца): без неё `shell` и исполняющие тулы
мастерской идут с правами пользователя, а текст режима «Песочница» обещает
обратное. Обещание, которое исполняет одна платформа из трёх, — не порт.

Механизм — тот же, которым на macOS огораживают себя Codex CLI и Claude Code:
`/usr/bin/sandbox-exec -p <профиль SBPL> -- <команда>`. В документации Apple
`sandbox-exec` помечен устаревшим, но это не отсутствие: бинарь есть на всех
актуальных macOS, а профили на том же языке исполняет ядро для каждого
приложения из App Store. Ставить App Sandbox подписью здесь нельзя — он
раздаётся приложению целиком, а нам нужна ограда на ОДИН процесс команды.

Контракт тот же, что у `fence.Container` и `fence_posix.Container`, и зовут его
из того же места (`fence.install`): `prepare()`, `describe()`,
`run(argv, cwd, timeout)`, `sync_mounts(rows)`. Плюс `profile()` — текст
профиля отдельным методом ради стендов: проверять то, что уйдёт ядру,
надёжнее, чем то, что вернул запуск (как `argv()` у bubblewrap).

    Windows                         macOS
    AppContainer + SID              профиль seatbelt на процесс команды
    icacls: поимённая выдача        (allow …) поимённо: видно ровно то, что названо
    job-объект (дети и таймаут)     своя сессия процессов + killpg по таймауту
    junction в workspace/mnt        символическая ссылка + (allow …) на цель

⚠ ПОРЯДОК ПРАВИЛ. В SBPL побеждает ПОСЛЕДНЕЕ совпавшее правило, поэтому запрет
секретов (`fence.secret_paths`) стоит ПОСЛЕ разрешения на память, внутри которой
они лежат. Но полагаться на одно только это знание, не проверив его на живой
машине, нельзя: разрешение памяти вдобавок само ИСКЛЮЧАЕТ секреты
(`require-not`). Под любой из двух трактовок порядка секреты закрыты, и живая
проверка (`tests/t_fence_macos.py::Live`) стережёт это на раннере macOS.

⚠ Что видно из системы. Профиль разрешает читать системные папки поимённо
(`/usr`, `/System`, `/Library`, `/private/etc`, …, `/opt/homebrew`,
`/Applications`), а всё остальное — только метаданные (`stat`). Дом владельца
(`~/Documents`, `~/Library`, ключи в связке) в этом списке нет: агент видит
своё (дом, память, душу), код продукта и то, что владелец смонтировал.

⚠ ВРЕМЕННЫЕ ПАПКИ ЗАКРЫТЫ. `/private/var/folders/<xx>/<hash>` — это кэши и
временные файлы ВСЕХ программ пользователя, и первый живой прогон на macOS
показал: разрешение на них накрывало и чужие папки, и код продукта (стенд
кладёт фикстуры именно в temp — и обязан класть их туда дальше). Поэтому в
профиле их нет ни на чтение, ни на запись; командам отдан `<workspace>/.tmp`
через TMPDIR/TMP/TEMP — его чтут питон, git, curl, pip, npm и `mktemp` с явным
шаблоном (`mktemp "$TMPDIR/x.XXXXXX"`). ⚠ Apple-овский `mktemp` БЕЗ шаблона идёт в
DARWIN_USER_TEMP_DIR (/var/folders/…/T) мимо TMPDIR — проверено живьём на раннере, — и
получает отказ: это свойство утилиты, а не ограды.
Библиотеки Apple, которые пишут по `confstr(_CS_DARWIN_USER_TEMP_DIR)` мимо
TMPDIR, получат отказ — это цена закрытой папки, и она названа здесь, а не
спрятана.

⚠ `/var`, `/tmp`, `/etc` на macOS — символические ссылки в `/private/…`, и
ядро сверяет ПУТЬ ПОСЛЕ РАЗБОРА ссылок; поэтому в профиле оба написания.

⚠ `bash -lc` и PATH. Login-shell читает `/etc/profile`, а тот зовёт
`/usr/libexec/path_helper`, который переставляет системные папки ВПЕРЁД
переданного PATH. Без поправки `python3` внутри ограды был бы
`/usr/bin/python3` — заглушкой, зовущей диалог «установить Command Line
Tools», — а не питон поставки. Поэтому для `bash -lc` PATH поставки
выставляется ещё раз ПЕРВОЙ командой (`_runtime_first`). Смысл тот же, что у
виндовой ограды: «PATH только из рантайма и системных папок».

⚠ `sandbox-exec` может отсутствовать (не macOS) или отказать (чужой профиль,
ошибка в нём). Тогда `prepare()` отказывается ВСЛУХ (`FenceUnavailable`), и
`fence.install` записывает причину в снимок устройства: «ограда не поднялась,
shell без ограды». Молча притворяться защитой нельзя — то же правило, что и на
Windows. Проба идёт НАСТОЯЩЕЙ командной строкой (`/usr/bin/true` под тем же
профилем): ошибка в профиле всплывает здесь, а не на первой команде агента.
"""
from __future__ import annotations

import atexit
import logging
import os
import shlex
import signal
import subprocess
import sys
import threading
from pathlib import Path

log = logging.getLogger("helene.fence.macos")

#: Единственное место, где живёт seatbelt. Не ищем по PATH: подложенный в
#: чужую папку `sandbox-exec` исполнился бы вместо системного.
SANDBOX_EXEC = "/usr/bin/sandbox-exec"

#: Что видно из системы — только на чтение. Поимённо, а не «всё»: дом
#: владельца сюда не входит. `/private/var/select` — куда указывает `/bin/sh`,
#: `/private/var/run` — resolv.conf; `/opt/homebrew` — инструменты Homebrew на
#: Apple Silicon (порт только для него).
SYSTEM_RO = ("/usr", "/System", "/Library", "/private/etc", "/private/var/db",
             "/private/var/run", "/private/var/select", "/dev", "/opt/homebrew",
             "/Applications")
#: Корни и ссылки, которые нужны, чтобы ДОЙТИ до разрешённого (только сама
#: папка, без содержимого): `/var` → `/private/var`, `/tmp` → `/private/tmp`.
SYSTEM_RO_LITERAL = ("/", "/private", "/private/var", "/private/tmp", "/tmp", "/var",
                     "/etc")
#: Временные папки пользователя — закрыты (см. шапку): ни одно правило профиля
#: не должно их называть. Константа нужна стендам, которые это стерегут.
USER_FOLDERS = "/private/var/folders"

#: Интерпретаторы, для которых login-shell переставляет PATH (см. шапку).
_LOGIN_SHELLS = ("bash", "sh", "zsh")

#: Службы macOS, к которым команда ходит через mach (launchd), — ПОИМЁННО, а не
#: `(allow mach-lookup)` целиком. Полный доступ открыл бы launchservicesd
#: (`open -a Safari …`, `open <файл>` поднимают процесс ВНЕ ограды — LaunchServices
#: просит его у launchd, и родитель ему уже не мы) и pasteboard (`pbpaste` читает
#: буфер обмена владельца). Здесь — тот минимум, которым живут bash, python, git,
#: curl: разбор пользователей и групп, доверие к сертификатам, настройки
#: CoreFoundation, разрешение имён. Образец — sandbox-профили Codex CLI и Chrome.
#: ⛔ launchservicesd и pasteboard в список НЕ входят намеренно.
MACH_SERVICES = (
    "com.apple.system.opendirectoryd.libinfo",
    "com.apple.system.opendirectoryd.membership",
    "com.apple.system.logger",
    "com.apple.system.notification_center",
    "com.apple.SecurityServer",
    "com.apple.trustd",
    "com.apple.trustd.agent",
    "com.apple.networkd",
    "com.apple.nehelper",
    "com.apple.nesessionmanager",
    "com.apple.mDNSResponder",
    "com.apple.cfprefsd.daemon",
    "com.apple.cfprefsd.agent",
    "com.apple.FSEvents",
    "com.apple.system.DirectoryService.libinfo_v1",
    "com.apple.system.DirectoryService.membership_v1",
)


class FenceUnavailable(RuntimeError):
    """Ограду поднять нечем — причина в тексте, и она уедет в снимок устройства."""


# --------------------------------------------------------------------------- #
#  Дети команды переживают движок, если их не снять
# --------------------------------------------------------------------------- #
#
# Команда идёт в СВОЕЙ сессии (`start_new_session`), поэтому `killpg` по группе
# движка (её шлёт сторож родителя `boot.watch_parent`, а на Windows — job-объект
# оболочки) до неё не достаёт: `sleep 3000 &` или `python -m http.server &` из
# bash пережили бы закрытие окна. Держим реестр живых pgid команд и снимаем их,
# когда движок уходит, — здесь то, что на Windows делает job-объект оболочки.
_LIVE_PGIDS: set[int] = set()
_LIVE_LOCK = threading.Lock()
_HOOKED = {"soft": False}
#: На Windows у `signal` нет SIGKILL, а модуль импортируют стенды и там; на самой
#: macOS реестр не пуст, и снятие идёт настоящим SIGKILL.
_SIGKILL = getattr(signal, "SIGKILL", 9)


def _register_pgid(pgid: int) -> None:
    with _LIVE_LOCK:
        _LIVE_PGIDS.add(pgid)


def _forget_pgid(pgid: int) -> None:
    with _LIVE_LOCK:
        _LIVE_PGIDS.discard(pgid)


def kill_live_children(sig: int = _SIGKILL) -> None:
    """Снять все живые группы процессов команд. Зовётся из atexit и мягкого выхода."""
    with _LIVE_LOCK:
        pgids = list(_LIVE_PGIDS)
        _LIVE_PGIDS.clear()
    for pgid in pgids:
        try:
            os.killpg(pgid, sig)
        except OSError:
            pass            # группа уже пуста — снимать нечего


atexit.register(kill_live_children)


def _ensure_soft_exit_hook() -> None:
    """Зацепить снятие детей за мягкий выход движка (SIGTERM → сторож родителя).

    `atexit` срабатывает на SystemExit, но НЕ на `os._exit` жёсткого добивания в
    `boot.watch_parent`; поэтому вешаемся ещё и на колбэк мягкого выхода
    (`boot.on_soft_exit`). Лениво и разово: boot к первой команде уже импортирован
    (его ставит `runner.main`), а на Windows fence_macos не грузится вовсе, так
    что виндовому поведению эта зацепка недоступна и не мешает.
    """
    if _HOOKED["soft"]:
        return
    _HOOKED["soft"] = True
    try:
        import boot
        boot.on_soft_exit(kill_live_children)
    except Exception:
        log.debug("сторож мягкого выхода не зацеплен — atexit всё равно снимет детей",
                  exc_info=True)


def _abs(path) -> Path:
    """Путь, каким его сверит ядро: абсолютный, со снятыми ссылками."""
    try:
        return Path(path).resolve()
    except OSError:
        return Path(path).absolute()


def _q(path) -> str:
    """Строка SBPL: в кавычках, обратный слэш и кавычка экранированы."""
    text = str(path).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


class Container:
    """Ограда для `shell` и прочих исполняющих тулов на macOS."""

    def __init__(self, install_root: Path, workspace: Path, network: bool,
                 tree: Path | None = None, secrets: list[Path] | None = None):
        self.root = Path(install_root)
        self.workspace = Path(workspace)
        self.network = bool(network)
        self.tree = Path(tree) if tree else self.workspace.parent
        self.secrets = [Path(p) for p in (secrets or [])]
        self.sandbox_exec = ""      # пусто до prepare(): ограда не подготовлена
        self.mounts: list[dict] = []
        self.mounts_fault = ""      # !="" — папки не встали в профиль, сняты (см. sync_mounts)
        self.sid_text = ""          # у AppContainer это SID; здесь — чем огорожено

    # --- подготовка ----------------------------------------------------------

    def prepare(self) -> None:
        if not Path(SANDBOX_EXEC).is_file():
            raise FenceUnavailable(
                f"нет sandbox-exec ({SANDBOX_EXEC}) — это не macOS, или система без "
                "seatbelt; сними галочку песочницы, чтобы продукт не обещал ограду")
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / ".tmp").mkdir(parents=True, exist_ok=True)
        self.sandbox_exec = SANDBOX_EXEC
        # Наличие бинаря ничего не доказывает: профиль может не скомпилироваться
        # (опечатка, незнакомое ядру правило). Проверяем НАСТОЯЩЕЙ командной
        # строкой — той самой, которой пойдут команды агента.
        try:
            probe = subprocess.run(self.argv(["/usr/bin/true"], self.workspace),
                                   capture_output=True, text=True, timeout=30,
                                   cwd=str(self.workspace), env=self.env())
        except (OSError, subprocess.SubprocessError) as exc:
            self.sandbox_exec = ""
            raise FenceUnavailable(f"sandbox-exec не запустился: {exc}") from exc
        if probe.returncode != 0:
            self.sandbox_exec = ""
            raise FenceUnavailable(
                "sandbox-exec есть, но ограда не поднимается: "
                + ((probe.stderr or probe.stdout).strip()[-300:] or f"код {probe.returncode}"))
        self.sid_text = f"seatbelt {SANDBOX_EXEC}"

    def describe(self) -> str:
        return (f"shell в seatbelt (sandbox-exec), сеть {'есть' if self.network else 'нет'}"
                + (f", смонтировано папок: {len(self.mounts)}" if self.mounts else "")
                + (f"; монтирования сняты (профиль не встал: {self.mounts_fault})"
                   if self.mounts_fault else ""))

    def writable_roots(self) -> list[Path]:
        """Куда shell из ограды вправе писать — для снимка устройства (иначе
        анатомия говорит «shell пишет никуда»). Тот же набор, что в правиле дома,
        плюс личный git агента; на Windows это заполняет `Container._grant`."""
        tree = _abs(self.tree)
        return [_abs(self.workspace), tree / "memory", tree / "soul", tree / ".git"]

    def _probe(self, reason: str) -> tuple[bool, str]:
        """Прогнать `/usr/bin/true` ТЕКУЩИМ профилем. -> (встал ли, причина отказа).

        Отдельно от `prepare()`: та проба шла БЕЗ монтирований (их ещё не было).
        """
        try:
            done = subprocess.run(self.argv(["/usr/bin/true"], self.workspace),
                                  capture_output=True, text=True, timeout=30,
                                  cwd=str(self.workspace), env=self.env())
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"{reason}: sandbox-exec не запустился: {exc}"
        if done.returncode != 0:
            return False, ((done.stderr or done.stdout).strip()[-300:]
                           or f"код {done.returncode}")
        return True, ""

    def sync_mounts(self, rows: list[dict]) -> None:
        """Папки владельца, открытые агенту сверх дома. Профиль собирается при
        КАЖДОМ запуске, а не один раз, — поэтому снятая владельцем папка исчезает
        из ограды сразу, а не до перезапуска.

        ⚠ `prepare()` пробовал профиль БЕЗ монтирований (их тогда не было). Если
        правило монтирования не компилируется, `sandbox-exec` падал бы на КАЖДОЙ
        команде, а снимок устройства говорил бы «container: True». Поэтому пробуем
        профиль С папками (`/usr/bin/true` тем же профилем) и при отказе снимаем
        ИМЕННО монтирования — со строкой в журнал и пометкой (`mounts_fault`,
        публикует `fence.Mounts._settle`), — а не ограду: без ограды shell пошёл
        бы вообще без ограничений.
        """
        self.mounts = [dict(row) for row in rows or []]
        self.mounts_fault = ""
        if not self.mounts or not self.sandbox_exec:
            return          # без папок профиль тот же, что уже проверил prepare()
        ok, err = self._probe("монтирования")
        if not ok:
            self.mounts_fault = err
            log.error("монтирование: профиль ограды с папками не встал (%s) — "
                      "оставляю ограду БЕЗ них, а не команду без ограды", err)
            self.mounts = []

    # --- профиль -------------------------------------------------------------

    def profile(self) -> str:
        """Профиль SBPL для этой установки. Отдельным методом ради стендов."""
        root, tree, home = _abs(self.root), _abs(self.tree), _abs(self.workspace)
        tmp = home / ".tmp"
        secrets = [_abs(p) for p in self.secrets]

        def rule(verb: str, ops: str, filters: list[str]) -> str:
            return f"({verb} {ops}\n    " + "\n    ".join(filters) + ")"

        lines = [
            "(version 1)",
            ";; Ограда shell-тула Hélène: закрыто всё, что не названо ниже.",
            "(deny default)",
            ";; Процессы: команда вправе запускать программы и детей; сигналы —",
            ";; только своим (тем же профилем). Наружу сигналов нет.",
            "(allow process-exec)",
            "(allow process-fork)",
            "(allow signal (target same-sandbox))",
            "(allow process-info* (target same-sandbox))",
            "(allow sysctl-read)",
            ";; Системные службы через mach — ПОИМЁННО (MACH_SERVICES): разбор",
            ";; пользователей и групп, доверие к сертификатам, настройки",
            ";; CoreFoundation, разрешение имён. Полный mach-lookup открыл бы",
            ";; launchservicesd (`open` поднимает процесс вне ограды) и pasteboard.",
            rule("allow", "mach-lookup", [f'(global-name "{n}")' for n in MACH_SERVICES]),
            "(allow ipc-posix*)",
            "(allow pseudo-tty)",
            ";; Метаданные любого пути: `stat`, `ls -l` по системе. Содержимое —",
            ";; только там, где разрешено ниже.",
            "(allow file-read-metadata)",
            rule("allow", "file-ioctl", [f"(literal {_q('/dev/tty')})",
                                         f"(literal {_q('/dev/null')})"]),
            ";; Система — только на чтение, поимённо.",
            rule("allow", "file-read*", [f"(subpath {_q(p)})" for p in SYSTEM_RO]
                 + [f"(literal {_q(p)})" for p in SYSTEM_RO_LITERAL]),
            ";; Код продукта и код агента — на чтение; корни — только пройти.",
            rule("allow", "file-read*", [f"(subpath {_q(root / 'app')})",
                                         f"(subpath {_q(root / 'tree')})",
                                         f"(subpath {_q(root / 'runtime')})",
                                         f"(literal {_q(root)})",
                                         f"(literal {_q(tree)})"]),
        ]
        # Дом агента — на запись: рабочая папка, память, душа и его личный
        # git-репозиторий (без него `git commit` из-под ограды падал бы на
        # index.lock — тот же список, что выдаёт AppContainer). Память
        # выдаётся С ИСКЛЮЧЕНИЕМ секретов: см. шапку про порядок правил.
        memory = tree / "memory"
        git_dir = tree / ".git"
        memory_filter = [f"(subpath {_q(memory)})"] + [
            f"(require-not (subpath {_q(s)}))" for s in secrets
            if s == memory or memory in s.parents]
        home_rw = [
            f"(subpath {_q(home)})",
            f"(subpath {_q(tmp)})",
            ("(require-all " + " ".join(memory_filter) + ")"
             if len(memory_filter) > 1 else memory_filter[0]),
            f"(subpath {_q(tree / 'soul')})",
            f"(literal {_q('/dev/null')})",
            f"(literal {_q('/dev/tty')})",
            f"(subpath {_q('/dev/fd')})",
        ]
        lines.append(";; Дом агента — на чтение и запись. Временные файлы — в")
        lines.append(";; <workspace>/.tmp (TMPDIR); /private/var/folders закрыт (см. шапку).")
        lines.append(rule("allow", "file-read* file-write*", home_rw))
        # Личный git агента (boot.seed_git): читать весь `.git` можно, писать —
        # всё, КРОМЕ config/hooks/info. Движок живёт ВНЕ ограды и на каждый shell
        # делает `git add -A`/`commit` без `--no-verify`; хук `.git/hooks/pre-commit`
        # или `core.hooksPath`/`core.fsmonitor` из `.git/config`, записанные ИЗ
        # ограды, исполнились бы СНАРУЖИ неё с полной средой — это побег. Чтение
        # config оставлено: без него git внутри ограды не работает; закрыта запись.
        git_guarded = ("(require-all " + f"(subpath {_q(git_dir)}) "
                       + " ".join(f"(require-not (subpath {_q(git_dir / name)}))"
                                  for name in ("config", "hooks", "info")) + ")")
        lines.append(";; Личный git агента: читать весь .git — можно…")
        lines.append(rule("allow", "file-read*", [f"(subpath {_q(git_dir)})"]))
        lines.append(";; …писать — всё, кроме config/hooks/info (иначе хук или")
        lines.append(";; core.hooksPath из ограды исполнит движок снаружи неё).")
        lines.append(rule("allow", "file-write*", [git_guarded]))
        # Монтирования владельца: слова доступа — те же, что в конфиге и в
        # `fence.mount_access`: "write" даёт запись, всё остальное — чтение.
        # Право даётся по РЕАЛЬНОМУ пути всегда: стык (`mnt/<имя>`, символическая
        # ссылка) — удобство, не право, как в виндовом `sync_mounts`. На POSIX
        # `link` появляется лишь после удачного `os.symlink`; требовать его
        # значило бы ронять доступ к папке владельца из-за неудавшегося стыка,
        # хотя по полному пути она открыта.
        for row in self.mounts:
            if row.get("error"):
                continue
            target = str(row.get("real") or row.get("path") or "")
            if not target:
                continue
            ops = "file-read* file-write*" if str(row.get("access")) == "write" else "file-read*"
            lines.append(f";; смонтировано владельцем: {target}")
            lines.append(rule("allow", ops, [f"(subpath {_q(_abs(target))})"]))
        # Секреты — ПОСЛЕ разрешений: последнее совпавшее правило побеждает.
        if secrets:
            lines.append(";; Секреты владельца закрыты поверх всего, что разрешено выше.")
            lines.append(rule("deny", "file-read* file-write*",
                              [f"(literal {_q(s)})" for s in secrets]
                              + [f"(subpath {_q(s)})" for s in secrets]))
        if self.network:
            lines.append(";; Сеть по ручке sandbox.network: наружу — куда угодно (как")
            lines.append(";; internetClient на Windows), а СЛУШАТЬ и ПРИНИМАТЬ — только на")
            lines.append(";; localhost. Полный network* дал бы bind/inbound на всех")
            lines.append(";; интерфейсах — команда из ограды раздала бы workspace по LAN.")
            lines.append("(allow network-outbound)")
            lines.append('(allow network-bind (local ip "localhost:*"))')
            lines.append('(allow network-inbound (local ip "localhost:*"))')
            lines.append(";; DNS: mDNSResponder слушает unix-сокет (входит в network-outbound).")
            lines.append('(allow network-outbound (path "/private/var/run/mDNSResponder"))')
            lines.append("(allow system-socket)")
        else:
            lines.append(";; Сети нет: ни наружу, ни к локальным сокетам (в том числе DNS).")
            lines.append("(deny network*)")
        return "\n".join(lines) + "\n"

    # --- запуск --------------------------------------------------------------

    def _runtime_path(self) -> str:
        """PATH внутри ограды: сначала поставка, потом системные папки."""
        runtime = _abs(self.root) / "runtime"
        return ":".join([str(runtime / "bin"), str(runtime / "git" / "bin"),
                         "/usr/bin", "/bin", "/usr/sbin", "/sbin"])

    def _runtime_first(self, argv: list[str]) -> list[str]:
        """`bash -lc …` → тот же `bash -lc`, но PATH и TMPDIR поставки — первой командой.

        Только для login-shell (`-lc`): именно он читает `/etc/profile`, а тот
        через `path_helper` ставит системные папки вперёд PATH. И TMPDIR: GUI-сессия
        macOS раздаёт свой `/var/folders/…/T` через launchd, и он переживает
        переданный в среде — а он закрыт оградой, там `mktemp` падает
        «Operation not permitted». Обе переменные выставляются ЗДЕСЬ, после
        `/etc/profile` (строка `-c` исполняется последней), поэтому берут верх.
        `sh -c` и голые argv берут среду как есть — там TMPDIR из `env()` не трут.
        """
        line = [str(a) for a in argv]
        if (len(line) >= 3 and line[1] == "-lc"
                and Path(line[0]).name.lower() in _LOGIN_SHELLS):
            tmp = _abs(self.workspace) / ".tmp"
            head = ("export PATH=" + shlex.quote(self._runtime_path()) + ':"$PATH"; '
                    + "export TMPDIR=" + shlex.quote(str(tmp))
                    + " TMP=" + shlex.quote(str(tmp))
                    + " TEMP=" + shlex.quote(str(tmp)) + "; ")
            return [line[0], "-lc", head + line[2]] + line[3:]
        return line

    def env(self) -> dict[str, str]:
        """Среда команды: дом и временные файлы в workspace, PATH из поставки.

        Своё — поверх, из среды раннера остаётся только то, без чего команды
        ведут себя странно (локаль, имя пользователя, часовой пояс). Ключи и
        ручки раннера (HELENE_*, PRAXIS_*) внутрь не попадают.
        """
        home = _abs(self.workspace)
        tmp = home / ".tmp"
        keep = ("USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SHELL")
        out = {k: os.environ[k] for k in keep if os.environ.get(k)}
        out.update({
            "PATH": self._runtime_path(),
            "HOME": str(home),
            "TMPDIR": str(tmp), "TMP": str(tmp), "TEMP": str(tmp),
            "HELENE_SANDBOX": "1",
            "PYTHONUTF8": "1",
        })
        # GUI-процесс, поднятый из Finder, не получает LANG — без него питон и
        # утилиты садятся на ASCII и спотыкаются о кириллицу в путях и выводе.
        out.setdefault("LANG", "en_US.UTF-8")
        return out

    def argv(self, argv: list[str], cwd: Path) -> list[str]:
        """Полная командная строка sandbox-exec. Отдельным методом ради стендов.

        `cwd` здесь не участвует: рабочую папку ставит `subprocess` (см. `run`),
        как и у bubblewrap `--chdir`; параметр оставлен ради одного контракта
        с ним.
        """
        return [self.sandbox_exec or SANDBOX_EXEC, "-p", self.profile(), "--"] \
            + self._runtime_first(list(argv))

    def run(self, argv: list[str], cwd: Path, timeout: float) -> tuple[str, int, bool]:
        """Запустить argv в ограде; -> (вывод, код, прервано по таймауту).

        Тот же кортеж, что у виндовой ограды: шим (`fence._SubprocessShim`) о
        различиях платформ ничего не знает и знать не должен.

        Команда идёт в СВОЕЙ сессии процессов (`start_new_session`): таймаут
        убивает всю группу (`killpg`), а не одного родителя, — внуки (сервер,
        поднятый из bash) не переживают команду, как и в job-объекте Windows.
        """
        if not self.sandbox_exec:
            raise FenceUnavailable("ограда не подготовлена: prepare() не звали")
        where = Path(cwd) if cwd else self.workspace
        if not where.is_dir():
            # Как у bubblewrap с `--chdir` в пустоту: это провал КОМАНДЫ, а не
            # ограды. OSError здесь снял бы ограду до конца сессии (шим читает
            # его как «контейнер не запустил команду»).
            return (f"рабочая папка не существует: {where}", 1, False)
        line = self.argv(argv, where)
        limit = max(1.0, float(timeout))
        _ensure_soft_exit_hook()
        proc = subprocess.Popen(line, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, cwd=str(where), env=self.env(),
                                start_new_session=True)
        _register_pgid(proc.pid)        # pgid == pid: своя сессия
        try:
            raw, _ = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired as expired:
            raw = _kill_tree(proc, expired)
            # Своей строки о таймауте НЕ добавляем: её пишет сам тул
            # («[прервано по таймауту]»), а две подряд — двойной текст на экране.
            # Как виндовая ограда, которая тоже отдаёт только вывод; факт таймаута
            # несёт третий элемент кортежа (timed=True).
            return (_decode(raw), 124, True)
        finally:
            # Прямой ребёнок вышел, но фоновый внук (`server &`) мог остаться в
            # группе: пустую — забываем, живую — держим, чтобы снять при выходе
            # движка (killpg в atexit/мягком выходе), как job-объект на Windows.
            try:
                os.killpg(proc.pid, 0)
            except OSError:
                _forget_pgid(proc.pid)
        return (_decode(raw), int(proc.returncode), False)


def _kill_tree(proc: subprocess.Popen, expired: subprocess.TimeoutExpired) -> bytes:
    """Убить всю группу процессов команды и забрать то, что она успела вывести."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)      # pgid == pid: своя сессия
    except ProcessLookupError:
        pass
    except OSError as exc:
        log.warning("группа процессов команды не убита (%s): добиваю родителя", exc)
        proc.kill()
    try:
        raw, _ = proc.communicate(timeout=5)
        return raw or b""
    except subprocess.TimeoutExpired:
        # Кто-то ушёл из группы (setsid) и держит наш вывод: отдаём, что было.
        proc.kill()
        return expired.stdout if isinstance(expired.stdout, bytes) else b""


def _decode(raw) -> str:
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8", "replace")


if __name__ == "__main__":
    # Показать профиль для типичной установки — чтобы прочитать глазами.
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "Applications" / "Helene"
    # ⚠ Список берётся из `fence.secret_paths`, а не повторяется здесь: копия
    # разошлась бы с оригиналом молча, и профиль, который человек читает глазами,
    # перестал бы показывать то, что стоит на живой машине (так и случилось с
    # ключом тела и файлами брокера — их тут не было).
    data = base / "data"
    from fence import secret_paths
    box = Container(base, data / "workspace", True, tree=data,
                    secrets=secret_paths(base, data))
    print(box.profile())
