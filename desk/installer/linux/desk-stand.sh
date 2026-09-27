#!/bin/sh
# Живой стенд Linux-тела в контейнере helene-linux-desk: X-сервер, оконный менеджер, шина
# сессии и доступности, затем сборка тела, его стенды Rust и живой стенд со свидетелями.
#
#   docker run --rm -v <дерево>/body:/src/body -v <репо>/desk:/src/desk \
#       -v helene-body-target:/target -v helene-cargo:/opt/cargo/registry \
#       helene-linux-desk sh /src/desk/installer/linux/desk-stand.sh
#
# JOBS — потоки cargo (по умолчанию 6: у ПК владельца память общая с его программами).
set -eu
JOBS="${JOBS:-6}"
export CARGO_TARGET_DIR=/target LANG=C.UTF-8 LC_ALL=C.UTF-8

echo "== сборка тела"
cd /src/body
cargo build -j "$JOBS" -p praxis-body -p praxis-bridge 2>&1 | tail -3

echo "== стенды Rust тела (чистые части, в том числе x11 и atspi)"
cargo test -j "$JOBS" -p praxis-body 2>&1 | grep -E "^test result|FAILED|panicked|failures:" || true

echo "== стол: Xvfb :99 1280x800, openbox, шина сессии, AT-SPI"
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
export DISPLAY=:99
for _ in $(seq 1 50); do xdpyinfo >/dev/null 2>&1 && break; sleep 0.1; done
eval "$(dbus-launch --sh-syntax)"
export DBUS_SESSION_BUS_ADDRESS
# GTK отдаёт дерево на шину доступности; включаем её как это делает рабочий стол.
dbus-send --session --print-reply --dest=org.a11y.Bus /org/a11y/bus \
    org.freedesktop.DBus.Properties.Set string:org.a11y.Status string:IsEnabled variant:boolean:true \
    >/dev/null 2>&1 || true
openbox >/tmp/openbox.log 2>&1 &
for _ in $(seq 1 50); do xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null | grep -q window && break; sleep 0.1; done
echo "   менеджер окон: $(xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null)"

echo "== живой стенд тела со свидетелями"
cd /src/desk
HELENE_BODY_BIN=/target/debug/praxis-body python3 tests/t_body_linux.py 2>&1
