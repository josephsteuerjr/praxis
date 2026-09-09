#!/usr/bin/env python3
"""Выкладка Пульта на сервер: тот же desk, что ставится на Windows, одним списком.

Зачем в репозитории. Сервер получал копию руками — скриптом из временной папки
очередной сессии, и состав «что именно считается Пультом» жил в голове того, кто
последним его писал. Теперь состав объявлен ЗДЕСЬ (``MODULE``), и его же берёт
поставка Hélène (``installer/build_dist.py`` кладёт те же файлы в ``app/``).
Расхождение между сервером и ПК с этого места видно диффом, а не памятью.

Что делает:

1. собирает фронты (можно пропустить: ``--skip-build``) и проверяет, что сборки
   на месте — выкладывать исходники Vite бессмысленно, страница будет пустой;
2. пакует ``MODULE`` в tar и кладёт во временный каталог на сервере;
3. распаковывает в ``<цель>.stage``, снимает копию живого каталога в
   ``<цель>-backup-<штамп>`` с квитанцией и подменяет содержимое;
4. перезапускает контейнер и проверяет живьём: ``/api/health``, ``/api/state``,
   ``/api/spend`` (на её данных отвечает несколько секунд — срок 90 с);
5. при любой ошибке возвращает прежний каталог из копии и перезапускает снова.

Чужие контейнеры и сети не трогает: только свой ``--container``.

    python server/deploy_desk.py --host 203.0.113.10 --user root
    python server/deploy_desk.py --dry-run     # что поедет, без единой записи

Пароль — в ``PRAXIS_DEPLOY_PASSWORD`` (или ключом ssh-agent). Нужен ``paramiko``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

DESK = Path(__file__).resolve().parent.parent

# Состав модуля desk: слева — что лежит в репозитории, справа — как это зовётся
# на сервере. Один список на обе установки; поставка Hélène кладёт то же самое в
# ``app/`` рядом с окном (build_dist.stage_payload).
MODULE: list[tuple[str, str]] = [
    ("deskapp.py", "deskapp.py"),
    ("deskd", "deskd"),
    ("app/dist", "static"),
    ("mobile/dist", "mobile"),
    ("miniapp/dist", "miniapp"),
]

# Что собирается перед выкладкой (npm --prefix <первый> run build).
FRONTS = [("app", "app/dist"), ("mobile", "mobile/dist"), ("miniapp", "miniapp/dist")]

SKIP = {"__pycache__", ".DS_Store"}


def log(msg: str) -> None:
    print(msg, flush=True)


def build_fronts() -> None:
    for prefix, dist in FRONTS:
        log(f"сборка {prefix}…")
        r = subprocess.run(["npm", "--prefix", prefix, "run", "build"], cwd=DESK,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           shell=os.name == "nt")
        if r.returncode != 0:
            raise SystemExit(f"{prefix}: сборка не прошла\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
        if not (DESK / dist / "index.html").is_file():
            raise SystemExit(f"{prefix}: после сборки нет {dist}/index.html")


def check_ready() -> None:
    for src, _ in MODULE:
        p = DESK / src
        if not p.exists():
            raise SystemExit(f"нет {src} — собери фронты или запусти без --skip-build")
        if p.is_dir() and (p.name == "dist") and not (p / "index.html").is_file():
            raise SystemExit(f"{src} без index.html — это не сборка Vite")


def pack() -> tuple[bytes, list[str]]:
    """tar модуля в память -> (байты, список файлов для квитанции)."""
    names: list[str] = []
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for src, dest in MODULE:
            path = DESK / src
            if path.is_file():
                tar.add(path, arcname=dest)
                names.append(dest)
                continue
            for item in sorted(path.rglob("*")):
                if any(part in SKIP for part in item.parts):
                    continue
                if item.is_file():
                    rel = f"{dest}/{item.relative_to(path).as_posix()}"
                    tar.add(item, arcname=rel)
                    names.append(rel)
    return buf.getvalue(), names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("PRAXIS_DEPLOY_HOST", ""))
    ap.add_argument("--user", default=os.environ.get("PRAXIS_DEPLOY_USER", "root"))
    ap.add_argument("--target", default="/opt/praxisdesk", help="каталог Пульта на сервере")
    ap.add_argument("--container", default="praxis-desk", help="контейнер, который перезапустить")
    ap.add_argument("--base", default="", help="адрес Пульта для проверок (по умолчанию https://<host>)")
    ap.add_argument("--key", default=os.environ.get("PRAXIS_DESK_TOKEN", ""), help="ключ канала для проверок")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.skip_build:
        build_fronts()
    check_ready()
    blob, names = pack()
    log(f"модуль: {len(names)} файлов, {len(blob) / 1e6:.1f} МБ в архиве")
    if args.dry_run:
        for n in names[:20]:
            log("  " + n)
        if len(names) > 20:
            log(f"  … ещё {len(names) - 20}")
        return 0
    if not args.host:
        raise SystemExit("нужен --host (или PRAXIS_DEPLOY_HOST)")

    try:
        import paramiko  # noqa: PLC0415 — зависимость только выкладки, не продукта
    except ImportError:
        raise SystemExit("нужен paramiko: python -m pip install paramiko")

    password = os.environ.get("PRAXIS_DEPLOY_PASSWORD") or None
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(args.host, username=args.user, password=password, timeout=30)

    def run(cmd: str, timeout: int = 120) -> tuple[int, str, str]:
        _in, out, err = client.exec_command(cmd, timeout=timeout)
        o = out.read().decode("utf-8", "replace")
        e = err.read().decode("utf-8", "replace")
        return out.channel.recv_exit_status(), o, e

    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    target = args.target.rstrip("/")
    stage = f"{target}.stage-{stamp}"
    backup = f"{target}-backup-{stamp}"
    tmp_tar = f"/tmp/desk-{stamp}.tar.gz"
    base = args.base or f"https://{args.host}"
    try:
        sftp = client.open_sftp()
        with sftp.open(tmp_tar, "wb") as sink:
            sink.write(blob)
        sftp.close()
        log(f"архив доставлен: {tmp_tar}")

        code, out, err = run(f"mkdir -p {stage} && tar xzf {tmp_tar} -C {stage} && ls {stage}")
        if code != 0:
            raise RuntimeError(f"распаковка не прошла: {err.strip() or out.strip()}")

        # Копия живого каталога с квитанцией: по ней видно, что и когда заменено.
        code, out, err = run(f"cp -a {target} {backup}")
        if code != 0:
            raise RuntimeError(f"копия не снялась: {err.strip()}")
        receipt = json.dumps({"at": stamp, "target": target, "files": len(names),
                              "from": "server/deploy_desk.py"}, ensure_ascii=False)
        run(f"cat > {backup}/receipt.json <<'EOF'\n{receipt}\nEOF")
        log(f"копия: {backup}")

        # Подменяем только объявленное: desk.env и данные рядом остаются на месте.
        for _src, dest in MODULE:
            code, out, err = run(f"rm -rf {target}/{dest} && cp -a {stage}/{dest} {target}/{dest}")
            if code != 0:
                raise RuntimeError(f"{dest}: не подменилось — {err.strip()}")
        log("файлы подменены")

        code, out, err = run(f"docker restart {args.container}", timeout=180)
        if code != 0:
            raise RuntimeError(f"контейнер не перезапустился: {err.strip()}")
        time.sleep(5)

        checks = [("/api/health", 30), ("/api/state", 60), ("/api/spend?days=1", 90)]
        for path, wait in checks:
            # Ключ приклеивается с учётом уже стоящего вопроса: «?days=1?key=…» —
            # это не адрес, и канал честно отвечал на него 403 (первый прогон 09.09).
            url = f"{base}{path}" + (("&" if "?" in path else "?") + f"key={args.key}" if args.key else "")
            code, out, err = run(
                f"curl -sS -m {wait} -o /dev/null -w '%{{http_code}}' '{url}'", timeout=wait + 30)
            if out.strip() != "200":
                raise RuntimeError(f"{path}: сервер ответил {out.strip() or err.strip()}")
            log(f"  {path}: 200")
        log("выложено")
        return 0
    except Exception as exc:  # откат — обязателен, а не «по возможности»
        log(f"⨯ {exc}")
        code, out, err = run(f"test -d {backup} && rm -rf {target} && cp -a {backup} {target} && echo restored")
        if "restored" in out:
            run(f"docker restart {args.container}", timeout=180)
            log(f"откат сделан из {backup}, контейнер перезапущен")
        else:
            log(f"⚠ откат НЕ сделан: копия {backup} недоступна — смотреть руками")
        return 1
    finally:
        run(f"rm -rf {stage} {tmp_tar}")
        client.close()


if __name__ == "__main__":
    sys.exit(main())
