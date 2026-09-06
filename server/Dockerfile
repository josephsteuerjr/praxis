# Харнесс Hélène на Linux: труба + раннер + дерево агента в одном контейнере.
# Контекст сборки — корень поставки (папка, куда распакован Helene-<версия>.zip):
#   docker compose -f server/docker-compose.yml up -d --build
# Windows-части поставки (exe, runtime/) сюда не копируются.
FROM python:3.12-slim

# git — личный репозиторий агента (data/.git, как MinGit на Windows);
# ffmpeg — голосовые для faster-whisper (PyAV его не требует, но телеграм-ogg
# и конвертация в media_audio идут через него).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && git config --global --add safe.directory '*'

WORKDIR /opt/helene
COPY requirements.txt server/requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-server.txt

COPY app/ ./app/
COPY tree/ ./tree/
COPY server/serverboot.py ./server/serverboot.py

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 HELENE_HOST=0.0.0.0
EXPOSE 8094
CMD ["python", "server/serverboot.py", "--config", "/opt/helene/helene.json"]
