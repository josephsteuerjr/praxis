#!/bin/sh
# Hélène на сервере — поставить или обновить ОДНОЙ командой.
#
#   sh Helene/server/install.sh                      поставить Hélène или обновить ту, что стоит
#   sh Helene/server/install.sh агент-с-ПК.zip       …и перенести агента с ПК (архив «Экспорт агента»)
#
# Запускать из распакованной поставки (папка Helene), от root. Нужен только Docker с compose.
# Повторный запуск ничего не ломает: поднимет, что лежит, и скажет, что стоит.
#
# Что делает сама:
#   * стоит Hélène — находит её по контейнеру агента, поднимает рядом исполнителя обновлений и
#     просит его обновить до этой версии. Исполнитель делает копию, переносит правки агента в
#     его собственном коде (и те, что жили внутри старого контейнера), запускает новую версию,
#     проверяет её, а не заработает — возвращает прежнюю сам. Память агента не трогается;
#   * не стоит — кладёт Hélène в /opt/helene (или в HELENE_DIR), разворачивает агента из архива,
#     если он дан, запускает и поднимает исполнителя;
#   * закрывает папку установки от других пользователей сервера (chmod 700).
#
# Имя сайта, по которому окно ходит к серверу (если оно есть), — один раз, переменной:
#   HELENE_HOSTS=helene.example.com sh Helene/server/install.sh
# Оно запоминается в server/.env установки.
set -eu

say() { printf '%s\n' "$*"; }
die() { printf '\n✗ %s\n' "$*" >&2; exit 1; }

# Команда молча; упала — показать её вывод (сборка и compose сыплют прогрессом в stderr).
quiet() {
  log=$(mktemp)
  if "$@" >"$log" 2>&1; then rm -f "$log"; return 0; fi
  tail -30 "$log" >&2; rm -f "$log"; return 1
}

# Версия a новее b? (1.10.0 > 1.9.9)
newer() {
  awk -v a="$1" -v b="$2" 'BEGIN { split(a, x, "."); split(b, y, ".");
    for (i = 1; i <= 3; i++) { if (x[i] + 0 > y[i] + 0) exit 0; if (x[i] + 0 < y[i] + 0) exit 1 }
    exit 1 }'
}

passport_version() {
  sed -n 's/^ *"version": *"\([0-9][0-9.]*\)".*/\1/p' "$1/helene-build.json" 2>/dev/null | head -1
}

REL=$(cd "$(dirname "$0")/.." && pwd)
[ -f "$REL/helene-build.json" ] || die "запускай из распакованной поставки: sh Helene/server/install.sh"
VERSION=$(passport_version "$REL")
[ -n "$VERSION" ] || die "не понял, какая это версия (в поставке нет helene-build.json)"

command -v docker >/dev/null 2>&1 || die "на сервере нет Docker. Поставь его (https://docs.docker.com/engine/install/) и запусти команду снова."
docker compose version >/dev/null 2>&1 || die "у Docker нет compose — он ставится вместе с Docker (пакет docker-compose-plugin)."
docker info >/dev/null 2>&1 || die "Docker не отвечает этому пользователю — запусти от root: sudo sh $0"

ARCHIVE=""
if [ $# -gt 0 ]; then
  [ -f "$1" ] || die "архива агента нет: $1"
  ARCHIVE=$(cd "$(dirname "$1")" && printf '%s/%s' "$(pwd)" "$(basename "$1")")
fi

CONTAINER=${HELENE_CONTAINER:-helene}
INSTALL=${HELENE_DIR:-}
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  WD=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$CONTAINER")
  [ -n "$WD" ] || die "контейнер $CONTAINER поднят не через docker compose — такой я не трогаю."
  [ -z "$INSTALL" ] || [ "$INSTALL" = "$(dirname "$WD")" ] || die "контейнер $CONTAINER — из $(dirname "$WD"), а HELENE_DIR=$INSTALL"
  INSTALL=$(dirname "$WD")
fi
INSTALL=${INSTALL:-/opt/helene}
export HELENE_DIR="$INSTALL" HELENE_CONTAINER="$CONTAINER"
say "Hélène $VERSION → $INSTALL"

remember_host() {
  # Имя сайта для окна — в server/.env установки: compose читает его сам при каждом подъёме.
  [ -n "${HELENE_HOSTS:-}" ] || return 0
  env_file="$INSTALL/server/.env"
  touch "$env_file"
  grep -v '^HELENE_HOSTS=\|^HELENE_PUBLIC_URL=' "$env_file" > "$env_file.tmp" || true
  printf 'HELENE_HOSTS=%s\nHELENE_PUBLIC_URL=%s\n' "$HELENE_HOSTS" \
    "${HELENE_PUBLIC_URL:-https://${HELENE_HOSTS%%,*}}" >> "$env_file.tmp"
  mv "$env_file.tmp" "$env_file"
}

up_agent() {
  say "· запускаю агента (первый раз сборка — несколько минут)"
  (cd "$INSTALL" && quiet docker compose -f server/docker-compose.yml up -d --build) \
    || die "агент не запустился — вывод выше"
}

up_updater() {
  say "· поднимаю исполнитель обновлений"
  (cd "$INSTALL" && quiet docker compose -f server/updater/docker-compose.yml up -d --build) \
    || die "исполнитель обновлений не запустился — вывод выше"
  tries=0
  until docker exec helene-updater python3 "$INSTALL/server/updater/updater.py" status >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [ "$tries" -ge 30 ]; then
      why=$(docker exec helene-updater python3 "$INSTALL/server/updater/updater.py" status 2>&1 || true)
      die "исполнитель обновлений не готов: $why"
    fi
    sleep 2
  done
}

lock_folder() {
  # Папки установки смонтированы в контейнер на запись (надзор там — root, агент с 1.2.5 —
  # свой пользователь): файл с битом setuid оттуда на хосте тоже setuid. Закрытая папка
  # установки не пускает к нему других пользователей хоста.
  chmod 700 "$INSTALL" 2>/dev/null || true
}

window_key() {
  tries=0
  while [ "$tries" -lt 30 ]; do
    line=$(docker logs "$CONTAINER" 2>&1 | grep -A1 'ключ окна' | tail -1 || true)
    if [ -n "$line" ]; then
      say ""
      say "Ключ для окна на ПК (Настройки → «Перенос» → «Подключить к серверу»):"
      say "  $line"
      return 0
    fi
    tries=$((tries + 1)); sleep 2
  done
}

# A failed first transfer can be continued without importing the archive twice.
# Never run this against a running agent; its later life belongs to the server.
prepare_archive() {
  [ -n "$ARCHIVE" ] || return 0
  sum=$(sha256sum "$ARCHIVE" | cut -d ' ' -f 1)
  marker="$INSTALL/.transfer-import.sha256"
  (cd "$INSTALL" && quiet docker compose -f server/docker-compose.yml build) \
    || die "сборка не удалась — агент ещё не запущен"
  if [ ! -f "$marker" ]; then
    say "· разворачиваю агента из архива"
    quiet docker run --rm -v "$INSTALL:/opt/helene" -v "$ARCHIVE:/carry.zip:ro" -w /opt/helene \
      --entrypoint python helene-helene app/localharness/carry.py import \
      --config /opt/helene/helene.json --archive /carry.zip \
      || die "архив агента не развернулся — данные сохранены; повтори перенос"
    printf '%s\n' "$sum" > "$marker"
  else
    [ "$(cat "$marker")" = "$sum" ] || die "эта установка уже перенесена из другого архива"
  fi
  say "· готовлю включённые слух и голос агента"
  connect_args=""
  [ "${HELENE_AUTOMATIC_CONNECTION:-}" != "1" ] || connect_args="--connect"
  quiet docker run --rm --init -v "$INSTALL:/opt/helene" -w /opt/helene \
    --entrypoint python helene-helene app/localharness/server_prepare.py --config /opt/helene/helene.json $connect_args \
    || die "слух или голос не подготовлен — агент ещё не запущен; повтори перенос"
}

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  if [ -f "$INSTALL/server/docker-compose.yml" ] && [ -d "$INSTALL/data" ]; then
    # Установка есть, а контейнера нет (его удалили): сначала поднять то, что стоит, —
    # обновлять дальше будет исполнитель, с переносом правок агента.
    say "· Hélène лежит в $INSTALL, но не запущена — запускаю то, что стоит"
    prepare_archive
    up_agent
  else
    say "· ставлю Hélène $VERSION в $INSTALL"
    mkdir -p "$INSTALL"
    if [ "$REL" != "$INSTALL" ]; then
      (cd "$REL" && tar cf - --exclude=./runtime --exclude='./*.exe' .) | (cd "$INSTALL" && tar xf -)
    fi
    [ -f "$INSTALL/helene.json" ] || cp "$INSTALL/server/helene.server.json" "$INSTALL/helene.json"
    mkdir -p "$INSTALL/data" "$INSTALL/server/models"
    remember_host
    prepare_archive
    up_agent
    up_updater
    lock_folder
    window_key
    say ""
    say "Готово: Hélène $VERSION запущена. Дальше обновления — кнопкой «Обновить» в окне"
    say "(Система → Обновление) или просто скажи агенту «обновись»."
    exit 0
  fi
fi

INSTALLED=$(passport_version "$INSTALL")
if [ "$REL" != "$INSTALL" ] && { [ ! -f "$INSTALL/server/updater/updater.py" ] \
    || { [ -n "$INSTALLED" ] && newer "$VERSION" "$INSTALLED"; }; }; then
  # Исполнителя нет (Hélène до 1.1.1) или он старше этой поставки — сначала он сам.
  mkdir -p "$INSTALL/server/updater"
  cp -R "$REL/server/updater/." "$INSTALL/server/updater/"
fi
up_updater
lock_folder

if [ -n "$INSTALLED" ] && ! newer "$VERSION" "$INSTALLED"; then
  if [ "$VERSION" = "$INSTALLED" ]; then
    say ""
    say "Hélène $INSTALLED уже стоит и работает. Исполнитель обновлений поднят — дальше обновления"
    say "кнопкой «Обновить» в окне (Система → Обновление) или просто скажи агенту «обновись»."
  else
    say ""
    say "Стоит Hélène $INSTALLED — она новее этой поставки ($VERSION). Ставить старую не буду."
  fi
  exit 0
fi

say "· обновляю ${INSTALLED:-?} → $VERSION (агент пропадёт на несколько минут)"
ID=$(docker exec helene-updater python3 "$INSTALL/server/updater/updater.py" plan "$VERSION") \
  || die "исполнитель не взял обновление: $ID"
docker exec helene-updater python3 "$INSTALL/server/updater/updater.py" watch "$ID" || exit 1
say ""
say "Дальше обновления — кнопкой «Обновить» в окне (Система → Обновление) или просто скажи"
say "агенту «обновись». Эта команда больше не нужна."
