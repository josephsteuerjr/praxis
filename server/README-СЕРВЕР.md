# Hélène на сервере: перенос агента и запуск в Docker

Агент живёт в одной папке `data/`. Перенос — это архив этой папки с
настройками; на сервере он разворачивается в такой же контейнер, что и на
Windows, только без оболочки: трубу и раннер держит `server/serverboot.py`, а
окно на ПК подключается к серверу по адресу и ключу. Обратный перенос
сервер → ПК — тем же архивом.

## 1. Экспорт на ПК

Настройки → карточка «Перенос» → «Экспорт агента», либо без окна:

```powershell
& "$env:LOCALAPPDATA\Programs\Helene\helene-setup.exe" --export --quiet
```

(`helene-setup.exe --export` находит установку по записи в реестре и зовёт
тот же помощник; путь к архиву — в `export.log` рядом с установщиком.) Или
напрямую:

```powershell
& "$env:LOCALAPPDATA\Programs\Helene\runtime\python.exe" "$env:LOCALAPPDATA\Programs\Helene\app\localharness\carry.py" export --config "$env:LOCALAPPDATA\Programs\Helene\helene.json"
```

Получается `helene-<агент>-<штамп>.zip` рядом с `helene.json`. **Внутри —
ключ модели, токен бота, вход ChatGPT и сессия Telegram** (иначе агент на новом
месте нем); паспорт `helene-carry.json` в архиве перечисляет их поимённо. Архив
не для пересылки посторонним. Что не едет: `data/body/` (тело руки `computer`
принадлежит машине), журналы, замок дерева, ключ окна, стыки `workspace/mnt/`.

## 2. Сервер: поставка и архив

На сервере нужны Docker и Compose. Папка проекта — своя, например
`/opt/helene`:

```bash
mkdir -p /opt/helene && cd /opt/helene
unzip -q ~/Helene-<версия>.zip && mv Helene/* . && rmdir Helene   # та же поставка, что и на Windows
cp server/helene.server.json helene.json
python3 app/localharness/carry.py import --config helene.json --archive ~/helene-<агент>-<штамп>.zip
```

Импорт разворачивает `data/` и сливает `helene.json`: из архива берутся блоки
агента (`agent`, `owner`, `model`, `telegram`, `relay`, `env`, `update`), а
местными остаются блоки хоста (`mode`, пути, `port`, `agent_mode`, `sandbox`,
`service`, `computer`, `phone`). На сервере ограда — сам контейнер, поэтому
`agent_mode` там `interactive`; тела руки `computer` нет; `phone.enabled: true`
— страница телефона той же трубой.

Реле подписки ChatGPT (`helene-relay.exe`) на сервере нет: агент с провайдером
`chatgpt` после переноса должен получить в `model` другой адрес (Anthropic-
или OpenAI-совместимый ключ) — либо реле поднимается на сервере отдельно, как
у Праксис (`/opt/relay`), и `model.base_url` смотрит на него.

## 3. Запуск

```bash
cd /opt/helene
HELENE_PUBLIC_URL=https://helene.example.com HELENE_HOSTS=helene.example.com \
HELENE_MODELS=/opt/praxis-models \
docker compose -f server/docker-compose.yml up -d --build
docker logs helene | head -20     # строка ключа окна печатается при старте
```

Труба слушает `127.0.0.1:8094` хоста. В мир её выпускает Caddy владельца —
одна строка в его `Caddyfile`, без правки чужих маршрутов:

```
helene.example.com {
    reverse_proxy 127.0.0.1:8094
}
```

и `caddy reload` (не `restart`). Или Tailscale: тогда `HELENE_HOSTS` — имя
машины в `*.ts.net`, и труба пускает его сама.

STT: контейнер получает те же `PRAXIS_STT_*`, что у Праксис, и модель
`faster-whisper-large-v3-turbo` (int8) из тома `/models` (`HELENE_MODELS` —
папка на хосте с `audio/whisper/faster-whisper-large-v3-turbo`). Модель
грузится при первой голосовой; `HELENE_STT_KEEP_LOADED=1` держит её в памяти
(~1,6 ГБ). Без модели голосовые не расшифровываются, дерево говорит об этом
само. На Windows STT нет намеренно.

## 4. Окно на ПК — к серверу

В `helene.json` на ПК (Настройки → «Перенос» → «Подключить к серверу» или
руками):

```json
{ "mode": "remote", "base": "https://helene.example.com", "key": "<ключ из docker logs>" }
```

После перезапуска окно ходит в серверную трубу; своих детей оболочка не
поднимает. Телефон — тот же адрес, `/m/`, спаривание по QR из окна. Вернуться
к локальному агенту — `"mode": "local"` (карточка «Перенос» делает это одной
кнопкой).

## 5. Обратный перенос

На сервере: `python3 app/localharness/carry.py export --config helene.json`,
архив — на ПК, там Настройки → «Перенос» → «Импорт агента» (или `carry.py
import`). Прежняя `data/` на ПК остаётся рядом как `data.before-<штамп>/`.

## Что здесь честно не сделано

- Один контейнер — один агент; второй агент = вторая папка и второй compose
  с другим портом.
- Обновление на сервере — распаковать новую поставку поверх (`app/`, `tree/`,
  `server/`) и `up -d --build`; `data/` и `helene.json` не трогаются.
- Реле ChatGPT в compose не входит.
