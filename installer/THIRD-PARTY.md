# Лицензии третьих сторон

Hélène собрана из открытых компонентов. Ниже — что именно едет в поставке и на
каких условиях.

Полные тексты лежат внутри самой поставки:

- Rust-крейты, статически влинкованные в `helene.exe`, `helene-setup.exe`,
  `helene-svc.exe`, `helene-bridge.exe` и `helene-body.exe` — в
  `licenses/rust/` (список крейтов и ссылки на тексты — в
  `licenses/rust/README.md`; собирается автоматически при сборке из `Cargo.lock`
  всех пяти).
- Пакеты Python — в `runtime/Lib/site-packages/<пакет>.dist-info/`.
- CPython — `runtime/LICENSE.txt`.

## Программа и установщик (Rust)

Основное, что видно в исходниках; полный список зависимостей вместе с их
транзитивными — в `licenses/rust/README.md`.

- Tauri 2 и его плагины (single-instance, window-state) — MIT или Apache-2.0
- tauri-winrt-notification — MIT
- serde, serde_json — MIT или Apache-2.0
- ureq — MIT или Apache-2.0
- winreg — MIT
- windows-service (служба) — MIT или Apache-2.0

## Интерфейс (TypeScript)

- motion — MIT
- qrcode — MIT
- Vite и TypeScript используются только при сборке и в поставку не входят

## Шрифты

- Source Serif 4 — SIL Open Font License 1.1
- Golos Text — SIL Open Font License 1.1
- PT Mono — SIL Open Font License 1.1
- Shantell Sans — SIL Open Font License 1.1

## Встроенный Python и пакеты

- CPython — Python Software Foundation License (`runtime/LICENSE.txt`)
- aiohttp, anthropic, openai, httpx, python-dotenv, pillow, pypdf, trafilatura,
  charset-normalizer, telethon и их зависимости — по их `dist-info/`
- pip остаётся в `runtime/` сознательно: без него рантайм нельзя починить на
  машине пользователя, не пересобирая всю поставку

## BusyBox (`runtime/bash.exe`)

`runtime/bash.exe` — это BusyBox for Windows (сборка Рона Йорстона),
**GPL-2.0**. Это отдельная программа-оболочка: Hélène её вызывает, но не
линкует, и на лицензию Hélène это не влияет.

**Письменное предложение по GPL-2.0 §3(b).** Владелец Hélène обязуется в
течение трёх лет с момента получения вами этой поставки передать любому
обратившемуся полную машиночитаемую копию соответствующего исходного кода
BusyBox на носителе или по сети, по цене не выше стоимости физической
передачи. Запрос — через issues репозитория
https://github.com/josephsteuerjr/helene/issues с указанием версии поставки
(`helene-build.json`). Тот же исходный код опубликован автором сборки на
frippery.org/busybox.

## Git (`runtime/git`)

`runtime/git` — это MinGit, минимальная сборка Git for Windows (вариант
busybox, без bash и perl), **GPL-2.0**. Отдельная программа: Hélène и агент её
вызывают (личный репозиторий агента в дереве данных, снимки его правок), но не
линкуют. Тексты лицензий лежат внутри: `runtime/git/LICENSE.txt`,
`runtime/git/mingw64/share/licenses`, `runtime/git/usr/share/licenses`.

**Письменное предложение по GPL-2.0 §3(b)** — то же, что для BusyBox выше:
в течение трёх лет с момента получения поставки владелец Hélène передаст
любому обратившемуся полную машиночитаемую копию исходного кода этой сборки
Git по цене не выше стоимости передачи; запрос — через issues репозитория
с указанием версии поставки. Тот же исходный код опубликован авторами на
https://github.com/git-for-windows/git (тег `v2.55.0.windows.5`).

## Реле подписки ChatGPT

- `helene-relay.exe` — MIT (исходники в репозитории автора)

## Тело руки `computer` (`helene-body.exe`, `helene-bridge.exe`)

Оба собраны из крейтов дерева агента — `tree/body/crates/praxis-body`,
`praxis-bridge` и `praxis-protocol`; исходники едут в этой же поставке. Их
зависимости (axum, tokio, rusqlite с bundled SQLite — Public Domain, крейт
`windows` — MIT или Apache-2.0, и остальные) перечислены в
`licenses/rust/README.md`.

Условия самого кода тела — те же, что у дерева: Apache-2.0 (см. «Код агента»
ниже). ⚠ В `tree/body/Cargo.toml` поле `license` этого workspace всё ещё
объявляет `PolyForm-Noncommercial-1.0.0` — это старая запись, оставшаяся с
тех пор, когда дерево ещё не было открыто под Apache-2.0; решение автора о
лицензии дерева (27.08.2026) её перекрывает, но поле в манифесте стоит
поправить в самом дереве (это правка хребта Праксис, здесь её не делают).

## Стороннее внутри дерева агента

- `tree/panel_static/3d-force-graph.min.js` — 3d-force-graph версии 1.80.0,
  https://github.com/vasturiano/3d-force-graph (MIT). В сборку упакованы
  three.js (MIT) и модули d3 (ISC). Сам минифицированный файл несёт только
  строку версии, без текстов лицензий: их условия — в перечисленных
  репозиториях. В продукте этот файл не используется: его читают серверные
  панели дерева агента (`panelapp.html`, `serverapp.py`), которых в Hélène нет

## Код агента

Дерево агента (`tree/`) — Apache-2.0. Полный текст лицензии — `tree/LICENSE`,
уведомление об авторстве — `tree/NOTICE` (и `NOTICE` в корне поставки).
