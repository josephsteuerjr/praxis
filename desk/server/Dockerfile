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

WORKDIR /opt/helene
COPY requirements.txt server/requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-server.txt

COPY app/ ./app/
COPY tree/ ./tree/
COPY server/serverboot.py ./server/serverboot.py

# Реле подписки ChatGPT — Linux-бинарь из поставки. Скобки делают его
# НЕОБЯЗАТЕЛЬНЫМ: поставка без реле (собранная там, где нет докера) соберётся
# так же, а `serverboot` скажет об отсутствии вслух, вместо молчания. Пара с
# `requirements.txt` нужна затем, что COPY по маске без единого совпадения —
# ошибка сборки; с гарантированным соседом маска просто ничего не приносит.
COPY requirements.txt helene-rela[y] ./
# Zip Windows-поставки не несёт бита исполнения — без chmod реле не запустится.
RUN chmod +x /opt/helene/helene-relay 2>/dev/null || true

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 HELENE_HOST=0.0.0.0
EXPOSE 8094
CMD ["python", "server/serverboot.py", "--config", "/opt/helene/helene.json"]
