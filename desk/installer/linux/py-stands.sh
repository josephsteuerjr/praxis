#!/bin/sh
# Стенды питона продукта рантаймом СОБРАННОГО пакета Linux (python-build-standalone из сборки),
# под обычным пользователем, по одному файлу со сроком: сводка — какие зелёные, какие нет.
#
#   docker run --rm --init -v helene-build-cache:/cache:ro -v <stream-linux>:/src:ro \
#       -v <stream-linux-tree>:/tree:ro helene-linux-desk sh /src/desk/installer/linux/py-stands.sh
#
# `--init` обязателен: без него первым процессом контейнера стоит runuser, он не подбирает
# осиротевших детей, и мягко вышедший ребёнок остаётся зомби — t_parent_watch видит его
# «живым». На рабочем столе сирот подбирает systemd.
set -eu
if [ "${1:-}" != "--user" ]; then
    id stand >/dev/null 2>&1 || useradd -m -s /bin/bash stand
    rm -rf /tmp/repo && mkdir /tmp/repo && cp -r /src/. /tmp/repo/ && chown -R stand /tmp/repo
    exec runuser -u stand -- sh "$0" --user
fi
PY=/cache/out/Helene/runtime/bin/python3
export HELENE_TREE_SRC=/tree PYTHONPATH=/tree PYTHONUTF8=1 LANG=C.UTF-8 LC_ALL=C.UTF-8
cd /tmp/repo/desk
ok=0; bad=0
for t in tests/t_*.py; do
    name=$(basename "$t")
    if timeout 300 "$PY" "$t" >/tmp/stand.log 2>&1; then
        ok=$((ok + 1)); echo "  ok      $name"
    else
        bad=$((bad + 1)); echo "  ПАДАЕТ  $name"
        grep -E "^(FAIL|ERROR):|Error:|AssertionError" /tmp/stand.log | head -6 | sed 's/^/          /'
    fi
done
echo "== итог: зелёных $ok, красных $bad"
