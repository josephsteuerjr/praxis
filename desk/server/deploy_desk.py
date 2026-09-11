#!/usr/bin/env python3
"""Выкладка Пульта на сервер: тот же пакет desk, что ставится на Windows.

Зачем в репозитории. Сервер получал копию руками — скриптом из временной папки
очередной сессии, и состав «что именно считается Пультом» жил в голове того, кто
последним его писал. Теперь состав, версия и зависимости объявлены в
``deskpkg.py``, а эта выкладка ставит СОБРАННЫЙ ПАКЕТ целиком — тот же, что
``installer/build_dist.py`` кладёт в ``app/`` поставки Hélène. Расхождение между
сервером и ПК с этого места видно отпечатком, а не памятью.

Что делает:

1. собирает фронты (можно пропустить: ``--skip-build``) и собирает пакет
   ``deskpkg.build(flavor="server")`` — без index.html сборки Vite это не пакет,
   и выкладка падает здесь, а не страницей-пустышкой у владельца;
2. пакует пакет в tar и кладёт во временный каталог на сервере;
3. распаковывает в ``<цель>.stage``, снимает копию живого каталога в
   ``<цель>-backup-<штамп>`` с квитанцией и подменяет ТОЛЬКО то, что объявлено
   пакетом (``desk.env``, данные и чужие копии рядом не трогает);
4. перезапускает контейнер и проверяет живьём: ``/api/health``, ``/api/state``,
   ``/api/spend`` (на её данных отвечает несколько секунд — срок 90 с), а по
   ``/api/state`` ещё и сверяет, что канал поднялся ИМЕННО с этим пакетом:
   версия и отпечаток в ответе — те же, что в собранном манифесте;
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
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

DESK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DESK))
import deskpkg  # noqa: E402 — объявление пакета desk лежит в корне репозитория

# Что собирается перед выкладкой (npm --prefix <первый> run build). Список
# берётся из состава пакета: фронт, который в пакет входит, обязан быть собран.
FRONTS = sorted({p.src.split("/")[0] for p in deskpkg.parts(deskpkg.SERVER) if p.dist})


def log(msg: str) -> None:
    print(msg, flush=True)


def build_fronts() -> None:
    for prefix in FRONTS:
        log(f"сборка {prefix}…")
        r = subprocess.run(["npm", "--prefix", prefix, "run", "build"], cwd=DESK,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           shell=os.name == "nt")
        if r.returncode != 0:
            raise SystemExit(f"{prefix}: сборка не прошла\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
        if not (DESK / prefix / "dist" / "index.html").is_file():
            raise SystemExit(f"{prefix}: после сборки нет {prefix}/dist/index.html")


def pack(root: Path) -> bytes:
    """tar собранного пакета в память — целиком, как он лежит."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for item in sorted(root.rglob("*")):
            if item.is_file():
                tar.add(item, arcname=item.relative_to(root).as_posix())
    return buf.getvalue()


def _channel_port(run) -> int | None:
    """Порт канала — из `desk.json` на сервере, а не из константы здесь.

    Константа разъехалась бы молча ровно в тот день, когда порт поменяют.
    """
    code, out, _ = run("cat /opt/praxisdesk/desk.json 2>/dev/null")
    if code != 0 or not out.strip():
        return None
    try:
        return int((json.loads(out) or {}).get("port") or 0) or None
    except (ValueError, TypeError):
        return None


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

    with tempfile.TemporaryDirectory(prefix="desk-pkg-") as tmp:
        pkg_root = Path(tmp) / "desk"
        man = deskpkg.build(pkg_root, deskpkg.SERVER, clean=True, log=log)
        blob = pack(pkg_root)
        names = sorted(man["files"])
        log(f"пакет desk {man['version']} ({man['flavor']}), отпечаток {man['digest'][:12]}: "
            f"{len(names)} файлов, {len(blob) / 1e6:.1f} МБ в архиве")
        for part in man["parts"]:
            log(f"  {part['name']:<14} {part['files']:>4}  {part['why']}")
        if args.dry_run:
            return 0
        if not args.host:
            raise SystemExit("нужен --host (или PRAXIS_DEPLOY_HOST)")
        return deploy(args, man, blob)


def deploy(args, man: dict, blob: bytes) -> int:
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
    # ⚠ Адрес живой проверки. Имя — проверяем снаружи (заодно ловится Caddy).
    # ГОЛЫЙ IP — снаружи по https не отвечает никогда: сертификат выписан на имя,
    # curl возвращает 000, и выкладка откатывает ЖИВОЙ канал как мёртвый. Так и
    # вышло 11.09: в журнале «Hélène слушает 0.0.0.0:8094», а деплой вернул
    # версию месячной давности. Для IP проверяем там, где канал слушает, —
    # изнутри сервера, и говорим об этом вслух.
    base = args.base
    if not base:
        looks_like_ip = re.fullmatch(r"[0-9.]+|\[[0-9a-fA-F:]+\]", args.host or "")
        if looks_like_ip:
            port = _channel_port(run) or 8094
            base = f"http://127.0.0.1:{port}"
            log(f"проверять будем изнутри сервера: {base} "
                "(в host голый IP — снаружи по https имени нет, и проверка "
                "снаружи означала бы откат живого канала)")
        else:
            base = f"https://{args.host}"
    top = deskpkg.top_level(man["flavor"])
    try:
        sftp = client.open_sftp()
        with sftp.open(tmp_tar, "wb") as sink:
            sink.write(blob)
        sftp.close()
        log(f"архив доставлен: {tmp_tar}")

        code, out, err = run(f"mkdir -p {stage} && tar xzf {tmp_tar} -C {stage} && ls {stage}")
        if code != 0:
            raise RuntimeError(f"распаковка не прошла: {err.strip() or out.strip()}")

        # Что лежит в цели сверх пакета — говорим вслух и не трогаем: там
        # desk.env с ключами и копии прошлых выкладок руками (mobile.old и
        # соседи от 06.09). Молча снести чужое — не наше дело.
        code, out, _err = run(f"ls -1 {target}")
        extra = [n for n in out.split() if n and n not in top and n != "receipt.json"]
        if extra:
            log("  рядом с пакетом (не трогаем): " + ", ".join(extra))

        # Копия живого каталога с квитанцией: по ней видно, что и когда заменено.
        code, out, err = run(f"cp -a {target} {backup}")
        if code != 0:
            raise RuntimeError(f"копия не снялась: {err.strip()}")
        receipt = json.dumps({"at": stamp, "target": target, "files": len(man["files"]),
                              "version": man["version"], "digest": man["digest"],
                              "from": "server/deploy_desk.py"}, ensure_ascii=False)
        run(f"cat > {backup}/receipt.json <<'EOF'\n{receipt}\nEOF")
        log(f"копия: {backup}")

        # Подменяем ровно то, что объявляет пакет: desk.env и данные рядом
        # остаются на месте.
        for name in top:
            code, out, err = run(f"rm -rf {target}/{name} && cp -a {stage}/{name} {target}/{name}")
            if code != 0:
                raise RuntimeError(f"{name}: не подменилось — {err.strip()}")
        log(f"файлы подменены ({len(top)} имён в корне)")

        code, out, err = run(f"docker restart {args.container}", timeout=180)
        if code != 0:
            raise RuntimeError(f"контейнер не перезапустился: {err.strip()}")
        time.sleep(5)

        checks = [("/api/health", 30), ("/api/state", 60), ("/api/spend?days=1", 90)]
        state_body = ""
        for path, wait in checks:
            # Ключ приклеивается с учётом уже стоящего вопроса: «?days=1?key=…» —
            # это не адрес, и канал честно отвечал на него 403 (первый прогон 09.09).
            url = f"{base}{path}" + (("&" if "?" in path else "?") + f"key={args.key}" if args.key else "")
            code, out, err = run(
                f"curl -sS -m {wait} -w '\\n%{{http_code}}' '{url}'", timeout=wait + 30)
            body, _, status = out.rpartition("\n")
            if status.strip() != "200":
                raise RuntimeError(f"{path}: сервер ответил {status.strip() or err.strip()}")
            if path == "/api/state":
                state_body = body
            log(f"  {path}: 200")

        # Канал поднялся именно с этим пакетом? Ответ читаем не «на глаз»:
        # версия и отпечаток в /api/state берутся из desk.json рядом с
        # deskapp.py, то есть из того, что мы сейчас положили.
        got = {}
        try:
            got = (json.loads(state_body) or {}).get("desk") or {}
        except ValueError:
            got = {}
        if not got:
            log("  ⚠ канал не назвал свой пакет (старая сборка без desk.json) — "
                "сверить нечем")
        elif got.get("digest") != man["digest"][:12] or got.get("version") != man["version"]:
            raise RuntimeError(
                f"канал живёт не тем пакетом: у него {got.get('version')} "
                f"{got.get('digest')}, а положено {man['version']} {man['digest'][:12]}")
        else:
            log(f"  пакет на сервере: desk {got['version']} {got['digest']}")
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
