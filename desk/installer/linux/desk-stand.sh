#!/bin/sh
# Живой стенд Linux-тела в контейнере helene-linux-desk: сборка тела и его стенды Rust
# (под root — так пишется общий кэш cargo), затем стол и живой стенд — ПОД ОБЫЧНЫМ
# ПОЛЬЗОВАТЕЛЕМ: в продукте тело живёт от имени владельца, а тело под root считает себя
# системным и уходит в маршрутизатор сессий, который есть только на Windows.
#
#   docker run --rm -v <дерево>/body:/src/body -v <репо>/desk:/src/desk \
#       -v helene-body-target:/target -v helene-cargo-reg:/opt/cargo/registry \
#       helene-linux-desk sh /src/desk/installer/linux/desk-stand.sh
#
# JOBS — потоки cargo (по умолчанию 6: у ПК владельца память общая с его программами).
set -eu
export LANG=C.UTF-8 LC_ALL=C.UTF-8

if [ "${1:-}" != "--desktop" ]; then
    JOBS="${JOBS:-6}"
    export CARGO_TARGET_DIR=/target
    echo "== сборка тела"
    cd /src/body
    cargo build -j "$JOBS" -p praxis-body -p praxis-bridge 2>&1 | tail -3
    echo "== стенды Rust тела (чистые части, в том числе x11 и atspi)"
    cargo test -j "$JOBS" -p praxis-body 2>&1 | grep -E "^test result|FAILED|panicked|failures:" || true
    id stand >/dev/null 2>&1 || useradd -m -s /bin/bash stand
    chmod -R a+rX /target/debug/praxis-body /target/debug/praxis-bridge 2>/dev/null || true
    exec runuser -u stand -- sh "$0" --desktop
fi

echo "== стол (пользователь $(id -un)): Xvfb :99 1280x800, openbox, шина сессии, AT-SPI"
cd /tmp
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp >/tmp/xvfb-stand.log 2>&1 &
export DISPLAY=:99
for _ in $(seq 1 50); do xdpyinfo >/dev/null 2>&1 && break; sleep 0.1; done
eval "$(dbus-launch --sh-syntax)"
export DBUS_SESSION_BUS_ADDRESS
# GTK отдаёт дерево на шину доступности; включаем её как это делает рабочий стол.
dbus-send --session --print-reply --dest=org.a11y.Bus /org/a11y/bus \
    org.freedesktop.DBus.Properties.Set string:org.a11y.Status string:IsEnabled variant:boolean:true \
    >/dev/null 2>&1 || true
openbox >/tmp/openbox-stand.log 2>&1 &
for _ in $(seq 1 150); do xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null | grep -q 'window id' && break; sleep 0.1; done
echo "   менеджер окон: $(xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null)"

echo "== живой стенд тела со свидетелями"
cd /src/desk
# STAND_ARGS — выбрать стенды (`Live.test_5…`), STAND_REPEAT — сколько раз подряд.
for _ in $(seq 1 "${STAND_REPEAT:-1}"); do
    HELENE_BODY_BIN=/target/debug/praxis-body python3 -W ignore tests/t_body_linux.py ${STAND_ARGS:-} 2>&1
done
