#!/bin/sh
# Проверка установки пакета в ЧИСТОМ контейнере дистрибутива (без systemd — службу проверяет
# ВМ): ставится ли пакет со всеми зависимостями, запускаются ли бинари на ЭТОЙ glibc, живёт ли
# рантайм, заводится ли дом владельца, снимается ли пакет, не трогая дом.
#
#   docker run --rm -v <папка с пакетами>:/pkg:ro debian:12   sh /pkg/install-check.sh deb
#   docker run --rm -v <папка с пакетами>:/pkg:ro rockylinux:8 sh /pkg/install-check.sh rpm
set -eu
kind="$1"
say() { printf '%s\n' "$*"; }
. /etc/os-release 2>/dev/null || true
say "== $PRETTY_NAME, glibc $(ldd --version 2>/dev/null | head -1 | awk '{print $NF}')"

if [ "$kind" = deb ]; then
    export DEBIAN_FRONTEND=noninteractive
    if grep -q buster /etc/os-release; then
        # Как у настоящей машины с Debian 10: основной архив И обновления безопасности (без них
        # у образа базовые пакеты новее архива, и procps «не будет установлен»).
        printf 'deb http://archive.debian.org/debian buster main\ndeb http://archive.debian.org/debian-security buster/updates main\n' \
            > /etc/apt/sources.list
        apt-get -o Acquire::Check-Valid-Until=false update -qq
    else
        apt-get update -qq
    fi
    apt-get install -y -qq /pkg/helene_*.deb >/tmp/install.log 2>&1 || { tail -30 /tmp/install.log; exit 1; }
    say "   поставлен: $(dpkg-query -W -f='${Package} ${Version}' helene)"
else
    if command -v dnf >/dev/null; then pm=dnf; else pm=yum; fi
    $pm install -y -q /pkg/helene-*.rpm >/tmp/install.log 2>&1 || { tail -30 /tmp/install.log; exit 1; }
    say "   поставлен: $(rpm -q helene)"
fi

say "== бинари на этой glibc"
/opt/helene/helene-svc unit | grep -q "ExecStart=/opt/helene/helene-svc daemon --owner %i" \
    && say "   helene-svc: запускается (печатает юнит)" || { say "   helene-svc: НЕ ЗАПУСКАЕТСЯ"; exit 1; }
for exe in helene-body helene-bridge; do
    /opt/helene/$exe --help >/dev/null 2>&1 && say "   $exe: запускается" || { say "   $exe: НЕ ЗАПУСКАЕТСЯ"; /opt/helene/$exe --help 2>&1 | head -3; exit 1; }
done
if ldd /opt/helene/helene-relay | grep -q "not found"; then
    say "   helene-relay: не хватает библиотек:"; ldd /opt/helene/helene-relay | grep "not found"; exit 1
fi
say "   helene-relay: все библиотеки на месте ($(ldd /opt/helene/helene-relay | grep -c '=>') .so, libssl внутри)"
/opt/helene/runtime/bin/python3 -c "import anthropic, openai, aiohttp, telethon, faster_whisper, piper, PIL; print('   рантайм: импорты живы')"
test -f "$(ls /lib/systemd/system/helene@.service /usr/lib/systemd/system/helene@.service 2>/dev/null | head -1)" && say "   юнит helene@.service на месте"
test -f /usr/share/polkit-1/actions/app.helene.policy && say "   политика polkit на месте"

say "== дом владельца (обычный пользователь)"
id owner >/dev/null 2>&1 || useradd -m owner
su owner -s /bin/sh -c 'helene-home' > /tmp/home.json
grep -q '"ok": *true' /tmp/home.json && say "   helene-home: $(cat /tmp/home.json | head -c 300)"
su owner -s /bin/sh -c 'test -L ~/.local/share/helene/runtime && test -f ~/.local/share/helene/helene.json && test -d ~/.local/share/helene/data' && say "   дом: ссылки, helene.json, data/ — на месте"
su owner -s /bin/sh -c 'helene-svc service state' 2>&1 | head -2 | sed 's/^/   service state: /'

say "== снятие"
if [ "$kind" = deb ]; then apt-get remove -y -qq helene >/dev/null 2>&1; else $pm remove -y -q helene >/dev/null 2>&1; fi
test ! -e /opt/helene/helene-svc && say "   программа снята"
su owner -s /bin/sh -c 'test -f ~/.local/share/helene/helene.json' && say "   дом владельца цел (пакет его не трогает)"
say "== ГОТОВО: $PRETTY_NAME"
