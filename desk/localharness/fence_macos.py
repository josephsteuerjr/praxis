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

import logging
import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path, PurePosixPath

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
#: Временные папки пользователя. Библиотеки Apple пишут туда мимо TMPDIR
#: (`confstr(_CS_DARWIN_USER_TEMP_DIR)`), и без записи сюда падают вещи вроде
#: `security` и `codesign`. Сужается до папки ЭТОГО пользователя, когда её
#: можно вывести из TMPDIR (`_user_folders`).
USER_FOLDERS = "/private/var/folders"

#: Интерпретаторы, для которых login-shell переставляет PATH (см. шапку).
_LOGIN_SHELLS = ("bash", "sh", "zsh")


class FenceUnavailable(RuntimeError):
    """Ограду поднять нечем — причина в тексте, и она уедет в снимок устройства."""


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


def _user_folders() -> str | None:
    """`/private/var/folders/<xx>/<hash>` этого пользователя — по TMPDIR раннера.

    Раннер живёт в сессии владельца, и его TMPDIR — `/var/folders/…/T/`. Папка на
    два уровня выше — весь набор временных папок пользователя (`T`, `C`, `X`).
    Не вывелось (TMPDIR пуст или в другом месте) — None: тогда разрешается вся
    `/private/var/folders`, как без сужения.
    """
    raw = (os.environ.get("TMPDIR") or "").strip()
    if not raw:
        return None
    # Ссылки снимаются только на macOS (`/var` → `/private/var`); стенды на
    # других платформах подают путь уже в том виде, в каком его увидит ядро,
    # а написание через ссылку принимается и без realpath.
    text = os.path.realpath(raw) if sys.platform == "darwin" else raw
    parts = PurePosixPath(text).parts
    if parts[:3] == ("/", "var", "folders"):
        parts = ("/", "private") + parts[1:]
    base = PurePosixPath(USER_FOLDERS).parts
    if parts[:len(base)] != base or len(parts) < len(base) + 2:
        return None
    return str(PurePosixPath(*parts[:len(base) + 2]))


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
                + (f", смонтировано папок: {len(self.mounts)}" if self.mounts else ""))

    def sync_mounts(self, rows: list[dict]) -> None:
        """Папки владельца, открытые агенту сверх дома. Здесь это просто список:
        профиль собирается при КАЖДОМ запуске, а не один раз, — поэтому снятая
        владельцем папка исчезает из ограды сразу, а не до перезапуска."""
        self.mounts = [dict(row) for row in rows or []]

    # --- профиль -------------------------------------------------------------

    def profile(self) -> str:
        """Профиль SBPL для этой установки. Отдельным методом ради стендов."""
        root, tree, home = _abs(self.root), _abs(self.tree), _abs(self.workspace)
        tmp = home / ".tmp"
        user_folders = _user_folders()
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
            ";; Системные службы через mach: без них не работают ни DNS, ни",
            ";; связка сертификатов, ни настройки CoreFoundation.",
            "(allow mach-lookup)",
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
        memory_filter = [f"(subpath {_q(memory)})"] + [
            f"(require-not (subpath {_q(s)}))" for s in secrets
            if s == memory or memory in s.parents]
        home_rw = [
            f"(subpath {_q(home)})",
            f"(subpath {_q(tmp)})",
            ("(require-all " + " ".join(memory_filter) + ")"
             if len(memory_filter) > 1 else memory_filter[0]),
            f"(subpath {_q(tree / 'soul')})",
            f"(subpath {_q(tree / '.git')})",
            f"(literal {_q('/dev/null')})",
            f"(literal {_q('/dev/tty')})",
            f"(subpath {_q('/dev/fd')})",
            f"(subpath {_q(user_folders or USER_FOLDERS)})",
        ]
        lines.append(";; Дом агента и временные папки — на чтение и запись.")
        lines.append(rule("allow", "file-read* file-write*", home_rw))
        if user_folders is not None:
            # Чужие временные папки под /private/var/folders — только метаданные;
            # своя выдана целиком строкой выше.
            lines.append(rule("allow", "file-read*", [f"(literal {_q(USER_FOLDERS)})"]))
        # Монтирования владельца: слова доступа — те же, что в конфиге и в
        # `fence.mount_access`: "write" даёт запись, всё остальное — чтение.
        for row in self.mounts:
            if row.get("error"):
                continue
            target = str(row.get("real") or row.get("path") or "")
            link = str(row.get("link") or "")
            if not target or not link:
                continue
            ops = "file-read* file-write*" if str(row.get("access")) == "write" else "file-read*"
            lines.append(f";; смонтировано владельцем: {link}")
            lines.append(rule("allow", ops, [f"(subpath {_q(_abs(target))})"]))
        # Секреты — ПОСЛЕ разрешений: последнее совпавшее правило побеждает.
        if secrets:
            lines.append(";; Секреты владельца закрыты поверх всего, что разрешено выше.")
            lines.append(rule("deny", "file-read* file-write*",
                              [f"(literal {_q(s)})" for s in secrets]
                              + [f"(subpath {_q(s)})" for s in secrets]))
        if self.network:
            lines.append(";; Сеть — по ручке sandbox.network владельца.")
            lines.append("(allow network*)")
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
        """`bash -lc …` → тот же `bash -lc`, но PATH поставки — первой командой.

        Только для login-shell (`-lc`): именно он читает `/etc/profile`, а тот
        через `path_helper` ставит системные папки вперёд. `sh -c` и голые argv
        берут PATH из среды как есть.
        """
        line = [str(a) for a in argv]
        if (len(line) >= 3 and line[1] == "-lc"
                and Path(line[0]).name.lower() in _LOGIN_SHELLS):
            head = "export PATH=" + shlex.quote(self._runtime_path()) + ':"$PATH"; '
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
        proc = subprocess.Popen(line, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, cwd=str(where), env=self.env(),
                                start_new_session=True)
        try:
            raw, _ = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired as expired:
            raw = _kill_tree(proc, expired)
            return (_decode(raw) + f"\n[оборвано по таймауту {timeout:.0f} с]", 124, True)
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
    box = Container(base, base / "data" / "workspace", True, tree=base / "data",
                    secrets=[base / "helene.json", base / "data" / "memory" / "llm.json",
                             base / "data" / "memory" / ".state" / "anatomy.json",
                             base / "data" / "relay", base / "data" / "telegram"])
    print(box.profile())
