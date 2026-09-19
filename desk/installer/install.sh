#!/bin/sh
# Hélène для macOS (Apple Silicon): установка, обновление и снятие.
#
#   curl -fsSL https://github.com/josephsteuerjr/praxis/releases/latest/download/install.sh | sh
#   sh install.sh [--from Helene-0.8.1-macos-arm64.zip] [--relaunch]
#   sh install.sh --uninstall [--purge]
#
# Что делает. Скачивает архив выпуска и его сумму в ~/Library/Caches/app.helene.install,
# сверяет сумму, распаковывает во временную папку (staging) и оттуда запускает
# мастер `Helene Setup.app` — тот же поток, что на Windows: мастер копирует
# программу в ~/Applications/Helene и отказывает, если попросить поставить
# поставку саму в себя. Если Hélène уже стоит (есть ~/Applications/Helene/helene.json),
# это обновление: работающая программа останавливается, мастер идёт тихо
# (`--install <json> --quiet`) и не трогает data/ и helene.json — решения для
# него читаются из уже установленного конфига.
#
# Почему curl, а не браузер. Подписи Developer ID у программы нет; файл, скачанный
# браузером, получает карантин, и Gatekeeper на Sequoia его блокирует. Файлы от
# curl карантина не получают; с распакованного он на всякий случай снимается.
#
# POSIX sh, без bash-измов: /bin/sh на macOS — это zsh или bash в sh-режиме, а
# скрипт идёт у человека, чьё окружение мы не знаем.
set -eu

HELENE_TAG_DEFAULT="v0.8.1"   # вписывает сборка (build_mac.stamp_install_sh); HELENE_TAG в среде — сильнее
HELENE_MACOS_MIN="14"         # тоже сборка: MACOS_MIN в build_mac.py (колёса голоса собраны под macOS 14)
REPO="josephsteuerjr/praxis"
PRODUCT="Hélène"
FOLDER="Helene"

TAG="${HELENE_TAG:-$HELENE_TAG_DEFAULT}"
VERSION="${TAG#v}"
ASSET="$FOLDER-$VERSION-macos-arm64.zip"
BASE_URL="https://github.com/$REPO/releases/download/$TAG"
# Корень установки. Оболочка при обновлении из окна передаёт свой (HELENE_ROOT):
# она знает, где стоит, лучше умолчания.
HOME_DIR="${HELENE_ROOT:-$HOME/Applications/$FOLDER}"
# Свой каталог, не app.helene.desk: тот — кэш самого Helene.app по bundle id
# (туда пишет WKWebView, снятие его сносит, а чистка кэшей macOS могла бы
# снести staging посреди установки).
CACHE="$HOME/Library/Caches/app.helene.install"
STAGING="$CACHE/staging"
SETUP_REL="Helene Setup.app/Contents/MacOS/helene-setup"
TMP="${TMPDIR:-/tmp}"

FROM=""
RELAUNCH=0
UNINSTALL=0
PURGE=0
ZIP=""
SETUP=""

say() { printf '%s\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

# После остановки старой копии любой выход с ошибкой обязан вернуть человеку
# программу: при обновлении из окна оболочка уже вышла сама, и `die` оставил бы
# его без Hélène. Ловушка на выход: отказ после остановки — назвать журналы и
# открыть прежний бандл обратно, если он цел (best effort). При обновлении из
# окна вывод этого скрипта оболочка пишет в <корень>/install-sh.log.
STOPPED=0
on_exit() {
    rc=$?
    if [ "$rc" -ne 0 ] && [ "$STOPPED" -eq 1 ]; then
        say "обновление не завершилось (код $rc); журналы: $STAGING/$FOLDER/install.log (мастер), $HOME_DIR/install-sh.log (этот скрипт, при обновлении из окна)"
        if [ -x "$HOME_DIR/Helene.app/Contents/MacOS/helene" ]; then
            say "запускаю обратно прежнюю копию: $HOME_DIR/Helene.app"
            open "$HOME_DIR/Helene.app" 2>/dev/null || say "прежняя копия не открылась: open \"$HOME_DIR/Helene.app\""
        else
            say "прежней копии в $HOME_DIR нет целиком — поставить заново: sh install.sh --from \"$ZIP\""
        fi
    fi
}
trap on_exit EXIT

usage() {
    cat <<EOF
$PRODUCT $VERSION для macOS (Apple Silicon)

  sh install.sh                     скачать выпуск $TAG и поставить (или обновить поверх)
  sh install.sh --from <zip>        взять архив с диска, не качать
  sh install.sh --relaunch          после обновления открыть $PRODUCT
  sh install.sh --uninstall         снять программу, данные оставить
  sh install.sh --uninstall --purge снять вместе с data/ и helene.json

Ставится в $HOME_DIR. Другой выпуск: HELENE_TAG=v0.8.1 sh install.sh
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --from)
            [ $# -ge 2 ] || die "--from требует путь к архиву"
            FROM="$2"; shift 2 ;;
        --from=*) FROM="${1#--from=}"; shift ;;
        --relaunch) RELAUNCH=1; shift ;;
        --uninstall) UNINSTALL=1; shift ;;
        --purge) PURGE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "неизвестный аргумент: $1 (--help покажет, что бывает)" ;;
    esac
done

sha_of() {
    shasum -a 256 "$1" | cut -d' ' -f1
}

check_platform() {
    [ "$(uname -s)" = "Darwin" ] || die "это установщик для macOS"
    arch="$(uname -m)"
    if [ "$arch" != "arm64" ]; then
        # Терминал под Rosetta называет себя x86_64 на настоящем Apple Silicon —
        # спрашиваем железо, а не оболочку.
        if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" != "1" ]; then
            die "нужен Mac на Apple Silicon (arm64), а это $arch: сборки для Intel нет"
        fi
    fi
    ver="$(sw_vers -productVersion 2>/dev/null || echo 0)"
    major="${ver%%.*}"
    case "$major" in
        ''|*[!0-9]*) die "не понял версию macOS: $ver" ;;
    esac
    [ "$major" -ge "$HELENE_MACOS_MIN" ] || die "нужна macOS $HELENE_MACOS_MIN или новее, а это $ver"
    for tool in ditto shasum xattr curl open ps awk; do
        command -v "$tool" >/dev/null 2>&1 || die "нет команды $tool"
    done
}

download() {
    mkdir -p "$CACHE"
    zip="$CACHE/$ASSET"
    sum="$CACHE/$ASSET.sha256"
    say "выпуск $TAG: беру сумму $ASSET.sha256…"
    curl -fsSL -o "$sum" "$BASE_URL/$ASSET.sha256" \
        || die "не скачалась сумма $BASE_URL/$ASSET.sha256 — есть ли в выпуске $TAG сборка для macOS?"
    want="$(head -n 1 "$sum" | cut -d' ' -f1)"
    [ -n "$want" ] || die "файл суммы пуст: $sum"
    # Кэш признаётся годным по сумме, а не по факту существования файла:
    # оборванная закачка иначе жила бы в нём вечно.
    if [ -f "$zip" ] && [ "$(sha_of "$zip")" = "$want" ]; then
        say "архив уже в кэше, сумма сходится"
    else
        rm -f "$zip" "$zip.part"
        say "качаю ${ASSET}…"
        curl -fL --progress-bar -o "$zip.part" "$BASE_URL/$ASSET" || die "не скачался $BASE_URL/$ASSET"
        got="$(sha_of "$zip.part")"
        if [ "$got" != "$want" ]; then
            rm -f "$zip.part"
            die "сумма не сошлась: в выпуске $want, скачано $got — файл подменён или оборван"
        fi
        mv "$zip.part" "$zip"
    fi
    ZIP="$zip"
}

verify_local() {
    [ -f "$FROM" ] || die "нет файла $FROM"
    if [ -f "$FROM.sha256" ]; then
        want="$(head -n 1 "$FROM.sha256" | cut -d' ' -f1)"
        got="$(sha_of "$FROM")"
        [ "$got" = "$want" ] || die "сумма $FROM не сходится с $FROM.sha256"
        say "сумма архива сходится"
    else
        say "рядом с $FROM нет .sha256 — сумма не сверялась"
    fi
    ZIP="$FROM"
}

stage() {
    rm -rf "$STAGING"
    mkdir -p "$STAGING"
    say "распаковываю…"
    ditto -x -k "$ZIP" "$STAGING" || die "архив не распаковался: $ZIP"
    SETUP="$STAGING/$FOLDER/$SETUP_REL"
    [ -x "$SETUP" ] || die "в архиве нет мастера ($FOLDER/$SETUP_REL) — это не архив $PRODUCT для macOS"
    [ -f "$STAGING/$FOLDER/helene-build.json" ] || die "в архиве нет паспорта helene-build.json"
    # От curl карантина нет, от браузера есть; снимаем в любом случае.
    xattr -dr com.apple.quarantine "$STAGING" 2>/dev/null || true
}

# Оболочка при обновлении из окна передаёт свой pid (HELENE_OLD_PID) и выходит
# сама через полторы секунды. Ждём её выхода до 10 с, а не гасим: процесс,
# который уходит сам, убивать незачем, и его дети умрут его рукой.
wait_old_shell() {
    pid="${HELENE_OLD_PID:-}"
    [ -n "$pid" ] || return 0
    case "$pid" in *[!0-9]*) return 0 ;; esac
    i=0
    while [ "$i" -lt 10 ] && kill -0 "$pid" 2>/dev/null; do
        sleep 1
        i=$((i + 1))
    done
}

# Всё, что запущено ИЗ папки установки: оболочка, движок, канал, реле — по пути
# исполняемого файла (comm), как это делает мастер, а не по строке командной
# строки: поиск по подстроке команды (pgrep по всей строке) гасил бы и
# `tail -f helene.log`, и редактор с открытым helene.json. Своя цепочка
# родителей исключается: скрипт могла запустить оболочка из этой же папки, и
# она выходит сама (wait_old_shell).
# ⚠ ДЕМОН СЛУЖБЫ ЗДЕСЬ НЕ ГАСИТСЯ (судьи 19.09). `helene-svc` живёт под launchd
# с `KeepAlive`: убитый нами процесс launchd поднимает обратно через секунду, и
# цикл ожидания ниже крутился бы все тридцать секунд впустую, а потом слал бы
# SIGKILL тому, кто уже другой. Снимает демон МАСТЕР — `launchctl bootout` под
# диалогом пароля (`setup/src/install.rs::service_op`), и это единственный путь,
# который работает. Поэтому `helene-svc` вырезан и из списка, и из ожидания, а
# владельцу сказано одной строкой, кто его снимет.
svc_pids() {
    ps -axo pid=,comm= 2>/dev/null | awk -v svc="$HOME_DIR/helene-svc" '
        {
            id = $1
            cmd = $2; for (i = 3; i <= NF; i++) cmd = cmd " " $i
            if (index(cmd, svc) == 1) print id
        }' || true
}

running_pids() {
    ps -axo pid=,ppid=,comm= 2>/dev/null | awk -v home="$HOME_DIR/" -v svc="$HOME_DIR/helene-svc" -v me="$$" '
        {
            id = $1; parent[id] = $2
            cmd = $3; for (i = 4; i <= NF; i++) cmd = cmd " " $i
            comm[id] = cmd; order[NR] = id
        }
        END {
            p = me; n = 0
            while (p > 1 && n < 64) { mine[p] = 1; p = parent[p]; n++ }
            for (k = 1; k <= NR; k++) {
                id = order[k]
                if (id in mine) continue
                if (index(comm[id], svc) == 1) continue
                if (index(comm[id], home) == 1) print id
            }
        }' || true
}

stop_running() {
    [ -n "$(svc_pids)" ] && say "служба работает — её снимет мастер (спросит пароль администратора)"
    pids="$(running_pids)"
    [ -n "$pids" ] || return 0
    say "останавливаю работающую ${PRODUCT}…"
    for pid in $pids; do
        kill "$pid" 2>/dev/null || true
    done
    i=0
    while [ "$i" -lt 30 ]; do
        [ -z "$(running_pids)" ] && return 0
        sleep 1
        i=$((i + 1))
    done
    for pid in $(running_pids); do
        kill -9 "$pid" 2>/dev/null || true
    done
    sleep 1
}

# Решения для тихого обновления — из уже установленного helene.json. Мастер
# переписывает своё (имя агента и владельца, модель, режим), и без этих
# значений он затёр бы выбор владельца умолчаниями. Пишет python из стейджа:
# jq на Mac из коробки нет, а разбирать JSON в sh нельзя. Файл читаем только
# владельцу (umask) — в нём ключ модели — и стирается сразу после мастера.
decisions_json() {
    py="$STAGING/$FOLDER/runtime/bin/python3"
    [ -x "$py" ] || return 1
    "$py" - "$HOME_DIR" "$1" <<'PY'
import json
import pathlib
import sys

home = pathlib.Path(sys.argv[1])
out = pathlib.Path(sys.argv[2])
# utf-8-sig: файл мог быть записан с BOM (Rust-мастер такое читает, json.loads — нет).
cfg = json.loads((home / "helene.json").read_text(encoding="utf-8-sig"))
model = cfg.get("model") or {}
base = str(model.get("base_url") or "")
key = str(model.get("key") or "")
name = str(model.get("model") or "")
relay = cfg.get("relay") or {}
if model.get("framework") == "anthropic":
    provider = "anthropic"
elif relay.get("enabled") or key.startswith("sk-frame-"):
    provider = "chatgpt"
elif key == "local":
    provider = "local"
else:
    provider = "api"
try:
    soul = (home / "data" / "soul" / "SOUL.md").read_text(encoding="utf-8")
except OSError:
    soul = ""
agent = str((cfg.get("agent") or {}).get("name") or "")
owner = str((cfg.get("owner") or {}).get("name") or "")
# Без конституции или имён тихо обновлять нельзя: мастер записал бы умолчания
# вместо решений владельца. Пусть решает человек — в окне мастера.
if not soul.strip() or not agent or not owner:
    sys.exit(2)
tg = cfg.get("telegram") or {}
setup = {
    "agent": agent,
    "owner": owner,
    "constitution": soul,
    "accepted": True,
    "provider": provider,
    "chatgpt_model": name if provider == "chatgpt" else "",
    "reasoning_effort": str(model.get("reasoning_effort") or ""),
    "api": {"base_url": base, "model": name, "key": key},
    "anthropic": {"base_url": base, "model": name, "key": key},
    "local": {"base_url": base, "model": name},
    "telegram": {"bot_token": str(tg.get("bot_token") or ""),
                 "owner_id": str(tg.get("owner_id") or "")},
    "agent_mode": str(cfg.get("agent_mode") or "sandbox"),
    # Служба: решение ВЛАДЕЛЬЦА, а не умолчание обновления. `installed.service`
    # — след мастера; он же и читается обратно. Поставь здесь False, и тихое
    # обновление молча сняло бы демон (мастер снимает прежнюю службу перед
    # копированием и ставит её обратно только по этому полю).
    "service": bool((cfg.get("installed") or {}).get("service")),
    # Нулевой сессии на macOS нет как механизма — и записать её отсюда нельзя.
    "session0": False,
    "computer": bool((cfg.get("computer") or {}).get("enabled")),
    "dir": str(home),
}
out.write_text(json.dumps(setup, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
PY
}

installed_version() {
    py="$HOME_DIR/runtime/bin/python3"
    if [ -x "$py" ] && [ -f "$HOME_DIR/helene-build.json" ]; then
        "$py" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("version", "?"))' \
            "$HOME_DIR/helene-build.json" 2>/dev/null || echo "?"
    else
        echo "?"
    fi
}

update() {
    say "$PRODUCT уже стоит в $HOME_DIR ($(installed_version)) — обновляю поверх; data/ и helene.json не трогаются"
    wait_old_shell
    stop_running
    # С этой строки любой отказ возвращает прежнюю копию (см. on_exit).
    STOPPED=1
    json="$CACHE/update-decisions.json"
    rm -f "$json"
    # В JSON решений — ключ модели: пишем его только владельцу. umask — на время
    # записи, мастеру возвращаем прежний: иначе права на файлы установки
    # зависели бы от того, обновление это или первая установка.
    old_umask="$(umask)"
    umask 077
    if decisions_json "$json"; then decided=1; else decided=0; fi
    umask "$old_umask"
    # Оба отказа ниже отдают дело мастеру с окном: человек доделывает в нём.
    # STOPPED=0 перед его открытием — иначе ловушка запустила бы ещё и прежний
    # Helene.app, и на экране оказались бы две программы сразу.
    if [ "$decided" -ne 1 ]; then
        rm -f "$json"
        say "не смог прочитать прежние решения из $HOME_DIR/helene.json — открываю мастер, обновление доделай в нём"
        say "журнал этого скрипта при обновлении из окна: $HOME_DIR/install-sh.log"
        STOPPED=0
        open "$STAGING/$FOLDER/Helene Setup.app" || true
        exit 1
    fi
    if "$SETUP" --install "$json" --quiet; then
        rm -f "$json"
        STOPPED=0
        say "обновлено до $(installed_version): $HOME_DIR"
    else
        rm -f "$json"
        # Мастер пишет install.log в корень поставки (папку с helene-build.json).
        say "тихое обновление не удалось — открываю мастер, доделай обновление в нём"
        say "журналы: $STAGING/$FOLDER/install.log (мастер), $HOME_DIR/install-sh.log (этот скрипт, при обновлении из окна)"
        STOPPED=0
        open "$STAGING/$FOLDER/Helene Setup.app" || true
        exit 1
    fi
    if [ "$RELAUNCH" -eq 1 ]; then
        open "$HOME_DIR/Helene.app" || say "не открылось: open \"$HOME_DIR/Helene.app\""
    else
        say "открыть: open \"$HOME_DIR/Helene.app\""
    fi
}

fresh() {
    say "первая установка: открываю мастер $PRODUCT — он поставит программу в $HOME_DIR"
    open "$STAGING/$FOLDER/Helene Setup.app"
}

uninstall() {
    [ -d "$HOME_DIR" ] || die "в $HOME_DIR ничего нет — снимать нечего"
    stop_running
    setup="$HOME_DIR/$SETUP_REL"
    if [ -x "$setup" ]; then
        # Код выхода ловим сами: под `set -e` отказ мастера ронял бы скрипт
        # молча, без слова о том, где журнал.
        if [ "$PURGE" -eq 1 ]; then
            "$setup" --uninstall --purge --quiet && rc=0 || rc=$?
        else
            "$setup" --uninstall --quiet && rc=0 || rc=$?
        fi
        [ "$rc" -eq 0 ] || die "мастер отказался снимать (код $rc); журнал: $TMP/helene-uninstall.log"
        say "снято мастером; журнал: $TMP/helene-uninstall.log"
    elif [ "$PURGE" -eq 1 ]; then
        rm -rf "$HOME_DIR"
        say "мастера в установке не было — папка $HOME_DIR удалена целиком"
    else
        # Без мастера: программу убираем, data/ и helene.json оставляем — это
        # память агента и его настройки, их снимает только --purge.
        for item in "$HOME_DIR"/* "$HOME_DIR"/.[!.]*; do
            [ -e "$item" ] || continue
            case "$(basename "$item")" in
                data|helene.json) ;;
                *) rm -rf "$item" ;;
            esac
        done
        say "мастера в установке не было — программа убрана, data/ и helene.json оставлены в $HOME_DIR"
    fi
    rm -rf "$STAGING"
}

main() {
    check_platform
    if [ "$UNINSTALL" -eq 1 ]; then
        uninstall
        exit 0
    fi
    if [ -n "$FROM" ]; then
        verify_local
    else
        download
    fi
    stage
    if [ -f "$HOME_DIR/helene.json" ]; then
        update
    else
        fresh
    fi
}

main
