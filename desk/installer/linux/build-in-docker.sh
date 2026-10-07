#!/bin/sh
# Сборка .deb в контейнере helene-linux-build из ЧИСТЫХ клонов веток (не из рабочих копий):
# паспорт получает настоящие коммиты, а тяжёлый ввод-вывод (npm, cargo, pip) идёт по диску
# контейнера, а не по медленной монтировке Windows.
#
#   docker run --rm \
#       -v <praxis-repo>/.git:/repo.git:ro -v <live>/.git:/live.git:ro \
#       -v helene-build-cache:/cache -v helene-cargo-reg:/opt/cargo/registry \
#       -v helene-npm-cache:/root/.npm -v <папка для пакета>:/out \
#       -e DESK_BRANCH=stream/linux-2809 -e TREE_BRANCH=stream/linux-tree-2809 \
#       helene-linux-build sh /repo-script/build-in-docker.sh [ключи build_linux.py]
set -eu
git config --global --add safe.directory '*'
rm -rf /work /tree
git clone -q --depth 1 -b "${DESK_BRANCH:-stream/linux-2809}" file:///repo.git /work
git clone -q --depth 1 -b "${TREE_BRANCH:-stream/linux-tree-2809}" file:///live.git /tree
echo "desk: $(git -C /work log --oneline -1)"
echo "дерево: $(git -C /tree log --oneline -1)"
cd /work/desk
export PIP_CACHE_DIR=/cache/pip PIP_ROOT_USER_ACTION=ignore
python3 installer/build_linux.py --tree /tree --out /cache/out "$@"
cp /cache/out/helene_*.deb /cache/out/helene-*.rpm /cache/out/*.sha256 /out/
cp /cache/out/Helene/helene-build.json /out/helene-build.json
ls -la /out
