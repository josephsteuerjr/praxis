# Исполнитель обновлений Hélène: клиент docker с плагином compose + python.
# Кода исполнителя в образе НЕТ: он читается из папки установки (server/updater/),
# поэтому обновляется вместе с поставкой, а образ меняется редко.
#
# docker:27-cli — клиент 27 договаривается о версии API и со старыми демонами (до
# 20.10), и с новыми (29); у 29-го клиента нижняя граница выше.
ARG DOCKER_CLI=27-cli
FROM docker:${DOCKER_CLI}

# git — только ради `git merge-file`: трёхстороннее слияние правок агента в его коде
# с новой версией (без него правку, которую тронул и выпуск, я бы только отдал агенту).
RUN apk add --no-cache python3 ca-certificates git

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
