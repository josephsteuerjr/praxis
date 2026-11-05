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
umask 077

say() { printf '%s\n' "$*"; }
die() { printf '\n✗ %s\n' "$*" >&2; exit 1; }

# Команда молча; упала — показать её вывод (сборка и compose сыплют прогрессом в stderr).
quiet() {
  log=$(mktemp)
  if "$@" >"$log" 2>&1; then rm -f "$log"; return 0; fi
  cp "$log" "$INSTALL/.install-last.log"
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

if [ -n "${HELENE_DIR:-}" ] && [ -f "$HELENE_DIR/server/.env" ]; then
  saved="$HELENE_DIR/server/.env"
  HELENE_PROJECT=${HELENE_PROJECT:-$(sed -n 's/^HELENE_PROJECT=\([a-z0-9][a-z0-9_-]*\)$/\1/p' "$saved" | tail -1)}
  HELENE_CONTAINER=${HELENE_CONTAINER:-$(sed -n 's/^HELENE_CONTAINER=\([a-zA-Z0-9][a-zA-Z0-9_.-]*\)$/\1/p' "$saved" | tail -1)}
fi
CONTAINER=${HELENE_CONTAINER:-${HELENE_PROJECT:-helene}}
PROJECT=${HELENE_PROJECT:-${HELENE_CONTAINER:-helene}}
INSTALL=${HELENE_DIR:-}
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  WD=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$CONTAINER")
  [ -n "$WD" ] || die "контейнер $CONTAINER поднят не через docker compose — такой я не трогаю."
  [ -z "$INSTALL" ] || [ "$INSTALL" = "$(dirname "$WD")" ] || die "контейнер $CONTAINER — из $(dirname "$WD"), а HELENE_DIR=$INSTALL"
  INSTALL=$(dirname "$WD")
  PROJECT=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$CONTAINER")
fi
INSTALL=${INSTALL:-/opt/helene}
UPDATER_CONTAINER="$CONTAINER-updater"
export HELENE_DIR="$INSTALL" HELENE_CONTAINER="$CONTAINER" HELENE_PROJECT="$PROJECT"
# Serialize all installers for this exact directory, including recovery.
case "$INSTALL" in /*) ;; *) die "HELENE_DIR must be absolute";; esac
[ ! -L "$INSTALL" ] || die "linked installation refused"
mkdir -p "$INSTALL"
[ "$(cd "$INSTALL" && pwd -P)" = "$INSTALL" ] || die "installation path is not canonical"
command -v flock >/dev/null 2>&1 || die "flock (util-linux) is required"
exec 9>"$INSTALL/.install.lock"
flock -n 9 || die "another installer owns this installation"
[ ! -e "$INSTALL/.window-transfer-stopped" ] || die "перенос остановлен владельцем для возврата; автоматический запуск запрещён"
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

remember_install() {
  env_file="$INSTALL/server/.env"
  touch "$env_file"
  grep -v '^HELENE_PROJECT=\|^HELENE_CONTAINER=\|^HELENE_DIR=' "$env_file" > "$env_file.tmp" || true
  printf 'HELENE_PROJECT=%s\nHELENE_CONTAINER=%s\nHELENE_DIR=%s\n' "$PROJECT" "$CONTAINER" "$INSTALL" >> "$env_file.tmp"
  for key in HELENE_PORT HELENE_MODELS HELENE_XTTS_RUNTIME HELENE_STT_THREADS HELENE_STT_KEEP_LOADED; do
    value=$(printenv "$key" || true)
    [ -n "$value" ] || continue
    grep -v "^$key=" "$env_file.tmp" > "$env_file.next" || true
    mv "$env_file.next" "$env_file.tmp"
    printf '%s=%s\n' "$key" "$value" >> "$env_file.tmp"
  done
  mv "$env_file.tmp" "$env_file"
}

up_agent() {
  say "· запускаю агента (первый раз сборка — несколько минут)"
  (cd "$INSTALL" && quiet docker compose -f server/docker-compose.yml up -d --build) \
    || die "агент не запустился — вывод выше"
}

up_updater() {
  say "· поднимаю исполнитель обновлений"
  (cd "$INSTALL" && quiet docker compose --env-file server/.env -f server/updater/docker-compose.yml up -d --build) \
    || die "исполнитель обновлений не запустился — вывод выше"
  tries=0
  until docker exec "$UPDATER_CONTAINER" python3 "$INSTALL/server/updater/updater.py" status >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [ "$tries" -ge 30 ]; then
      why=$(docker exec "$UPDATER_CONTAINER" python3 "$INSTALL/server/updater/updater.py" status 2>&1 || true)
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
    if [ -s "$INSTALL/data/.serverboot/desk-token" ]; then
      say ""
      say "Канал готов. Ключ окна хранится приватно: $INSTALL/data/.serverboot/desk-token"
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
  if [ -f "$marker" ] && [ "$(cat "$marker")" != "$sum" ]; then
    die "эта установка уже перенесена из другого архива"
  fi
  say "· проверяю или восстанавливаю импорт агента"
  quiet docker run --rm --network none -v "$INSTALL:/opt/helene" -v "$ARCHIVE:/carry.zip:ro" -w /opt/helene \
    --entrypoint python "$PROJECT-helene" server/import_agent.py --root /opt/helene --archive /carry.zip \
    || die "импорт не завершён — архив и прежние файлы сохранены; повтори перенос"
  say "· готовлю подключения, слух и голос агента"
  connect_args=""
  [ "${HELENE_AUTOMATIC_CONNECTION:-}" != "1" ] || connect_args="--connect"
  quiet docker run --rm --init -v "$INSTALL:/opt/helene" \
    -v "$INSTALL/component-store:/opt/helene/.local/share/helene/component-store-v1" \
    -e XDG_DATA_HOME=/opt/helene/.local/share -e HELENE_COMPONENT_PROGRAM=/opt/helene \
    -e HELENE_SUPERVISOR=serverboot -w /opt/helene \
    --entrypoint python "$PROJECT-helene" app/localharness/server_prepare.py --config /opt/helene/helene.json $connect_args \
    || die "подключения или компоненты не подготовлены — агент ещё не запущен; данные сохранены, повтори перенос"
}

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  if [ -f "$INSTALL/server/docker-compose.yml" ] && [ -d "$INSTALL/data" ]; then
    # Установка есть, а контейнера нет (его удалили): сначала поднять то, что стоит, —
    # обновлять дальше будет исполнитель, с переносом правок агента.
    say "· Hélène лежит в $INSTALL, но не запущена — запускаю то, что стоит"
    remember_install
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
    remember_install
    prepare_archive
    up_agent
    up_updater
    lock_folder
    window_key
    say ""
    say "Готово: установка Hélène $VERSION поднята; сохранённый Stop остаётся в силе. Дальше обновления — кнопкой «Обновить» в окне"
    say "(Система → Обновление) или просто скажи агенту «обновись»."
    exit 0
  fi
fi

if [ -n "$ARCHIVE" ] && docker inspect "$CONTAINER" >/dev/null 2>&1; then
  [ -f "$INSTALL/.transfer-import.sha256" ] || die "работающий агент без квитанции импорта — повторный импорт запрещён"
  [ "$(cat "$INSTALL/.transfer-import.sha256")" = "$(sha256sum "$ARCHIVE" | cut -d ' ' -f 1)" ] || die "эта установка уже перенесена из другого архива"
fi
INSTALLED=$(passport_version "$INSTALL")
if [ "$REL" != "$INSTALL" ] && { [ ! -f "$INSTALL/server/updater/updater.py" ] \
    || { [ -n "$INSTALLED" ] && newer "$VERSION" "$INSTALLED"; }; }; then
  # Исполнителя нет (Hélène до 1.1.1) или он старше этой поставки — сначала он сам.
  mkdir -p "$INSTALL/server/updater"
  cp -R "$REL/server/updater/." "$INSTALL/server/updater/"
fi
remember_install
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
ID=$(docker exec "$UPDATER_CONTAINER" python3 "$INSTALL/server/updater/updater.py" plan "$VERSION") \
  || die "исполнитель не взял обновление: $ID"
docker exec "$UPDATER_CONTAINER" python3 "$INSTALL/server/updater/updater.py" watch "$ID" || exit 1
say ""
say "Дальше обновления — кнопкой «Обновить» в окне (Система → Обновление) или просто скажи"
say "агенту «обновись». Эта команда больше не нужна."
