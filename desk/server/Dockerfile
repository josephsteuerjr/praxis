# Харнесс Hélène на Linux: канал + раннер + дерево агента в одном контейнере.
# Контекст сборки — корень поставки (папка, куда распакован Helene-<версия>.zip):
#   docker compose -f server/docker-compose.yml up -d --build
# Windows-части поставки (exe, runtime/) сюда не копируются.
FROM python:3.12-slim

# git — личный репозиторий агента (data/.git, как MinGit на Windows);
# ffmpeg — голосовые для faster-whisper (PyAV его не требует, но телеграм-ogg
# и конвертация в media_audio идут через него).
# procps — `free`, `ps`, `top`: агент зовёт их рукой `shell`, осматривая дом,
# и в slim-образе спотыкался на первом же `free` (живой ход 06.09 на VPS).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ffmpeg ca-certificates procps \
    && rm -rf /var/lib/apt/lists/* \
    && git config --global --add safe.directory '*'

# 1.2.5 (отчёт Йоны): агент — не root. Раннер и его руки — `helene`, канал — `desk`, общая
# группа данных; надзор (root) раздаёт права при старте (serverboot.separate). git — для
# всех пользователей (safe.directory), а не только для root.
RUN groupadd -g 10000 helene-data \
    && useradd -u 10001 -g 10000 -m -d /home/helene -s /bin/bash helene \
    && useradd -u 10002 -g 10000 -m -d /home/desk -s /usr/sbin/nologin desk \
    && git config --system --add safe.directory '*'
WORKDIR /opt/helene
COPY requirements.txt server/requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-server.txt

# Код в образе — запасной: compose монтирует поверх `app/` и `tree/` с диска сервера
# (там их правит агент, и правки переживают пересборку). Здесь он нужен, чтобы образ
# был целым сам по себе: репетиция расширений исполнителя гоняет его без монтирования.
COPY app/ ./app/
COPY tree/ ./tree/
COPY helene-build.json ./helene-build.json
COPY server/serverboot.py server/supervisor_policy.py server/emergency_stop.py ./server/

COPY helene-relay ./helene-relay
RUN chmod +x /opt/helene/helene-relay

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 HELENE_HOST=0.0.0.0
EXPOSE 8094
CMD ["python", "server/serverboot.py", "--config", "/opt/helene/helene.json"]
