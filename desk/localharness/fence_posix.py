# -*- coding: utf-8 -*-
"""Ограда на POSIX: bubblewrap там, где на Windows стоит AppContainer.

Зачем. Порт на Linux и macOS решено делать «основой, без UIA и брокера», но
ограда в основу входит обязательно: без неё файловые руки и `shell` идут с
правами пользователя, а текст режима «Песочница» обещает обратное. Обещание,
которое исполняет одна платформа из двух, — это не порт, а полпорта.

Контракт тот же, что у `fence.Container`, и зовут её из того же места
(`fence.install`): `prepare()`, `describe()`, `run(argv, cwd, timeout)`,
`sync_mounts(rows)`. Разница только в механизме:

    Windows                         POSIX
    AppContainer + SID              пространства имён bubblewrap
    icacls: поимённая выдача        --bind: видно ровно то, что связано
    job-объект (дети и таймаут)     --unshare-pid + --die-with-parent
    junction в workspace/mnt        --bind в ту же точку внутри namespace

⚠ Что здесь НЕ так, как на Windows, и почему это честно. На Windows ограда
СНИМАЕТ права у контейнера, оставляя папку на месте; здесь папки, которых
агенту видеть не положено, просто не связываются — их в пространстве имён нет
вовсе. Секреты (`helene.json`, `memory/llm.json`, `relay/`, `telegram/`)
закрываются поверх связанной памяти пустышкой: `--bind /dev/null <файл>` и
`--tmpfs <папка>`. Список секретов — тот же самый, `fence.secret_paths`: две
правды о том, что прятать, разъехались бы молча.

⚠ bubblewrap может отсутствовать или быть запрещён (нет непривилегированных
user namespace — так бывает в старых Debian и в контейнерах без нужных прав).
Тогда `prepare()` отказывается ВСЛУХ, и `fence.install` записывает причину в
снимок устройства: «ограда не поднялась, shell без ограды». Молча притворяться
защитой нельзя — это то же правило, что и на Windows.

macOS: не здесь. Там ограду ставит seatbelt (`fence_macos`, тот же контракт),
и `fence.install` выбирает модуль по платформе — этот на darwin не зовётся.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("helene.fence.posix")

BWRAP = "bwrap"

#: Что видно из системы — только на чтение. `-try` там, где папки может не быть:
#: раскладка /lib и /lib64 отличается у Debian, Arch и NixOS.
SYSTEM_RO = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/etc/alternatives",
             "/etc/ssl", "/etc/ca-certificates", "/etc/pki")
#: Что нужно только при разрешённой сети.
NETWORK_RO = ("/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf")


class FenceUnavailable(RuntimeError):
    """Ограду поднять нечем — причина в тексте, и она уедет в снимок устройства."""


class Container:
    """Ограда для `shell` и прочих исполняющих рук на POSIX."""

    def __init__(self, install_root: Path, workspace: Path, network: bool,
                 tree: Path | None = None, secrets: list[Path] | None = None):
        self.root = Path(install_root)
        self.workspace = Path(workspace)
        self.network = bool(network)
        self.tree = Path(tree) if tree else self.workspace.parent
        self.secrets = [Path(p) for p in (secrets or [])]
        self.bwrap = ""
        self.mounts: list[dict] = []
        self.sid_text = ""          # у AppContainer это SID; здесь — чем огорожено

    # --- подготовка ----------------------------------------------------------

    def prepare(self) -> None:
        found = shutil.which(BWRAP)
        if not found:
            raise FenceUnavailable(
                "нет bubblewrap (bwrap) — поставь его (apt install bubblewrap) "
                "или сними галочку песочницы, чтобы продукт не обещал ограду")
        self.bwrap = found
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / ".tmp").mkdir(parents=True, exist_ok=True)
        # Наличие бинаря ничего не доказывает: непривилегированные user
        # namespace выключены во многих сборках ядра и в чужих контейнерах.
        # Проверяем НАСТОЯЩЕЙ командной строкой — той самой, которой пойдут
        # команды агента. Синтетическая проба этого не ловит: первая её
        # редакция связывала только `/usr` и падала на `/bin/true`, потому что
        # в Debian `/bin` — символическая ссылка, и без связывания её в
        # пространстве имён нет вовсе.
        true_bin = shutil.which("true") or "/usr/bin/true"
        probe = subprocess.run(self.argv([true_bin], self.workspace),
                               capture_output=True, text=True, timeout=30)
        if probe.returncode != 0:
            raise FenceUnavailable(
                "bubblewrap есть, но ограда не поднимается: "
                + (probe.stderr.strip()[-200:] or f"код {probe.returncode}"))
        self.sid_text = f"bubblewrap {found}"

    def describe(self) -> str:
        return (f"shell в bubblewrap, сеть {'есть' if self.network else 'нет'}"
                + (f", смонтировано папок: {len(self.mounts)}" if self.mounts else ""))

    def sync_mounts(self, rows: list[dict]) -> None:
        """Папки владельца, открытые агенту сверх дома. Здесь это просто список:
        связываются они при КАЖДОМ запуске, а не один раз, — поэтому снятая
        владельцем папка исчезает из ограды сразу, а не до перезапуска."""
        self.mounts = [dict(row) for row in rows or []]

    # --- запуск --------------------------------------------------------------

    def argv(self, argv: list[str], cwd: Path) -> list[str]:
        """Полная командная строка bwrap. Отдельным методом ради стендов:
        проверять то, что уйдёт ядру, надёжнее, чем то, что вернул запуск."""
        home = self.workspace
        tmp = home / ".tmp"
        out = [self.bwrap, "--die-with-parent", "--new-session",
               "--unshare-user", "--unshare-ipc", "--unshare-pid", "--unshare-uts",
               "--unshare-cgroup-try"]
        if not self.network:
            out += ["--unshare-net"]
        out += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
        for path in SYSTEM_RO:
            out += ["--ro-bind-try", path, path]
        if self.network:
            for path in NETWORK_RO:
                out += ["--ro-bind-try", path, path]
        # Код продукта и дерева — на чтение; дом агента — на запись.
        for path in (self.root / "app", self.root / "tree", self.root / "runtime"):
            out += ["--ro-bind-try", str(path), str(path)]
        out += ["--bind", str(home), str(home)]
        for name in ("memory", "soul"):
            path = self.tree / name
            out += ["--bind-try", str(path), str(path)]
        # Секреты закрываются ПОВЕРХ связанной памяти: пустышкой вместо файла и
        # пустой tmpfs вместо папки. Список — из `fence.secret_paths`.
        for secret in self.secrets:
            if secret.is_dir():
                out += ["--tmpfs", str(secret)]
            else:
                out += ["--bind-try", "/dev/null", str(secret)]
        for row in self.mounts:
            target = str(row.get("path") or "")
            link = str(row.get("link") or "")
            if not target or not link:
                continue
            # Слова доступа — те же, что в конфиге и в `fence.mount_access`:
            # "write" даёт запись, всё остальное — только чтение.
            flag = "--bind-try" if str(row.get("access")) == "write" else "--ro-bind-try"
            out += [flag, target, link]
        out += ["--setenv", "HOME", str(home),
                "--setenv", "TMPDIR", str(tmp),
                "--setenv", "TMP", str(tmp),
                "--setenv", "HELENE_SANDBOX", "1",
                "--setenv", "PYTHONUTF8", "1",
                "--chdir", str(cwd if cwd else home), "--"]
        return out + list(argv)

    def run(self, argv: list[str], cwd: Path, timeout: float) -> tuple[str, int, bool]:
        """Запустить argv в ограде; -> (вывод, код, прервано по таймауту).

        Тот же кортеж, что у виндовой ограды: шим (`fence._SubprocessShim`) о
        различиях платформ ничего не знает и знать не должен.
        """
        if not self.bwrap:
            raise FenceUnavailable("ограда не подготовлена: prepare() не звали")
        line = self.argv(argv, Path(cwd) if cwd else self.workspace)
        try:
            done = subprocess.run(line, capture_output=True, timeout=max(1.0, float(timeout)))
        except subprocess.TimeoutExpired as expired:
            # bwrap — первый процесс в своём пространстве, и его смерть уносит
            # всё дерево: отдельного job-объекта, как на Windows, не нужно.
            text = _decode(expired.stdout) + _decode(expired.stderr)
            return (text + f"\n[оборвано по таймауту {timeout:.0f} с]", 124, True)
        return (_decode(done.stdout) + _decode(done.stderr), done.returncode, False)


def _decode(raw) -> str:
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8", "replace")
