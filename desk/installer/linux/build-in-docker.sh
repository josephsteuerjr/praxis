#!/bin/sh
# Серверная сборка DEB/RPM из явного чистого sanitized source ref:
# паспорт получает настоящие коммиты, а тяжёлый ввод-вывод (npm, cargo, pip) идёт по диску
# контейнера, а не по медленной монтировке Windows.
#
#   docker run --rm \
#       -v <source>/.git:/repo.git:ro \
#       -v helene-build-cache:/cache -v helene-cargo-reg:/opt/cargo/registry \
#       -v helene-npm-cache:/root/.npm -v <папка для пакета>:/out \
#       -e DESK_BRANCH=<accepted-release-ref> \
#       helene-linux-build sh /repo-script/build-in-docker.sh [ключи build_linux.py]
set -eu
: "${DESK_BRANCH:?Укажи DESK_BRANCH принятого sanitized source; Linux сборки идут на сервере}"
git config --global --add safe.directory '*'
if [ -e /work ]; then
    echo '/work уже существует: используй новый изолированный контейнер сборщика' >&2
    exit 1
fi
git clone -q --depth 1 -b "$DESK_BRANCH" file:///repo.git /work
echo "desk: $(git -C /work log --oneline -1)"
cd /work/desk
export PIP_CACHE_DIR=/cache/pip PIP_ROOT_USER_ACTION=ignore
# Default candidate staging validates the complete canonical inventory.
python3 installer/build_linux.py --out /cache/out "$@"
python3 - "$@" <<'PY'
import shutil, sys
from pathlib import Path
sys.path.insert(0,'installer')
import build_linux as build
args=build.arg_parser().parse_args(['--out','/cache/out',*sys.argv[1:]])
version,_=build.deskpkg.product_version()
names=build.asset_names(version)
root=Path(args.out).resolve()
for kind in ('deb','rpm') if args.packager=='both' else (args.packager,):
    for name in (names[kind],names[kind+'_sha256']):
        shutil.copy2(root/name,Path('/out')/name)
shutil.copy2(root/'Helene/helene-build.json','/out/helene-build.json')
PY
ls -la /out
