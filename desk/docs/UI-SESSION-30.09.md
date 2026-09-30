# Управление движком и ходом, 30.09.2026

Работа от desk `0de4fe408d2a47bf04b2542f120e4e4565648e3d`, ветка
`fix/ui-session-3009`. Код ядра не изменён. Это проверенный исходный кандидат;
установленная Hélène и owner stop не изменялись.

## Поведение

- В чате Hélène Alt+Enter начинает и заканчивает запись голосового. Оно остаётся
  вложением до отправки; сочетание не отправляет текстовый черновик. Подсказка
  микрофона и `aria-keyshortcuts` показывают сочетание. Удержание клавиш, IME и
  повторный старт во время проверки слуха/разрешения микрофона не запускают
  вторую запись. Смена комнаты/раздела или остановка движка во время ожидания
  разрешения отменяет старт и освобождает полученный stream. Уже начатую запись
  можно закончить после остановки движка. Shared UI безопасен без mic в remote.
- В шапке окна — перезапуск движка и, когда оболочка поддерживает owner stop,
  одна кнопка остановки/возобновления. Окно и черновик остаются открытыми.
  Переход подтверждается наблюдением, а не ответом «команда принята».
- Над вводом — маленькое «Думает...» по `model_started` или «Работает...» по
  инструменту/границе. Статус не зависит от раскрытой панели. Завершённый manifest
  убирает его до сброса busy heartbeat. Подробности действий остаются в панели.
- «Остановить ход» адресует foreground run текущей комнаты. Остановка всего
  движка — отдельная команда. Старый interrupt receipt сравнивается с точным ID
  запроса; смена комнаты/хода снимает старый статус. Просьба «Прочитать сейчас»
  сверяет ID хода и на клиенте, и у текущего процессного шага на сервере.
- Новые сообщения подтверждаются `source_id`, совпадающим с именем durable
  записки. Повторный текст, расшифровка и подвал вложений не служат identity.
  Отправка означает приём в очередь; архив означает приём раннером, а не обещание,
  что модель уже прочла сообщение. Legacy text fallback ограничен временем.
- Один цикл голосового прогресса для слуха и речи, без пересечения запросов
  внутри карточки. Поздний ответ после ухода не рисует/не запускает опрос.
  Первый mount и возврат в настройки возобновляют чтение. Выбранные значения
  переживают polling и переход между разделами, пока не сохранены/перечитаны.
- Ширина панелей сохраняет показанное значение; лентой управляет её scroller,
  без дополнительного quiet `scrollTop` после загрузки. Прочитанные ошибки
  сохраняются при закрытии окна. Шапка и статус переносятся в узком центре.

## Добавления к контракту

- `/api/state.runner.pid` и необязательная `activity`:
  `{run_id, phase, tool, chat_id, kind, event_seq}`.
- `/api/say.source_id` = `note:<published filename stem>`; тот же ID у входного
  `memory/groups/<room>.jsonl`. Старые строки не получают выдуманного ID.
- `/api/interrupt-step`: необязательный `run_id`; несовпадение не прерывает
  последующий ход. Старые вызовы без ID совместимы.
- Tauri `owner_state`: `{agent_id, supported, stopped, runner_alive, pid, note}`.
  Windows PID читается без завершения процесса: SYNCHRONIZE/limited query,
  zero wait и birth time относительно heartbeat. Неизвестное состояние остаётся
  неизвестным. Флаг Windows owner stop читается отдельно от остановленного API.
- Tauri `engine_restart` публикует существующий `helene.supervisor.v1` request
  атомарно (MoveFileExW replace на Windows, rename POSIX). Владелец остановил
  движок — перезапуск отказывает, сначала требуется явное возобновление.
- Resume/restart ждут ответ API на запрос, начатый после принятия команды.
  Windows дополнительно сверяет живой native PID с API; restart ждёт другой PID.
  Через 60 секунд неподтверждённая операция называется неподтверждённой;
  позднее правильное наблюдение может завершить её. Web/старые оболочки скрывают
  отсутствующий native stop; чужой харнесс не получает ложную кнопку restart.

## Проверки и воспроизведение

С рабочего корня `desk`, без запуска агента:

```powershell
npm --prefix app run build
npm --prefix remote run build
node --no-warnings --test app/test/*.mjs
python -X utf8 tests/t_message_identity.py
python -X utf8 tests/t_reader_activity.py
python -X utf8 tests/t_turn_inbox.py
python -X utf8 tests/t_inbox_attachments.py
cargo check --manifest-path shell/Cargo.toml --locked --jobs 2
cargo test --manifest-path shell/Cargo.toml --locked --jobs 2 engine_restart_request_speaks_the_channel_format -- --exact tests::engine_restart_request_speaks_the_channel_format --test-threads=1
```

Последний Rust unit — только временные файлы и read-only probe собственного PID,
без дочерних процессов. Полные Forge/process suites в native Windows runner
запрещены; использовать Linux boundary согласно AGENTS.md.

Для визуального replay после сборки:

```powershell
node app/test/fixtures/ui-session-server.mjs
```

Открыть напечатанный localhost URL. `/__fixture/status` подтверждает identity
стенда. `/__fixture/set` принимает сценарные phase/busy/run/room/receipt,
holdInterrupt/voiceDelay/voiceBusy. Подключён реальный dist UI; API, WebSocket
и native bridge — синтетические. В fixture нет доступа к установленным данным,
службе, модели или дочерним процессам. Закрыть Ctrl+C после проверки.

Проверены в browser replay: model→tool→idle; скрытая panel и фокус; stop/resume/
restart с сохранением черновика; scoped stop, чужая/поздняя квитанция и новый ход;
повторный текст; одновременный STT/TTS и выбор голоса после возврата; широкий
viewport и минимум 900×600, включая обе открытые панели.

Native compile и безопасный unit выполнены на Windows. UI replay проверяет shared
frontend и наблюдаемый контракт, не заменяет UAC/service lifecycle в установленной
WebView. Native macOS/Linux сборки и canary не запускались. Electron preview по-прежнему
не реализует Tauri owner commands; неподдерживаемый stop скрывается.

Дополнение 01.10: Alt+Enter проверен исполнением настоящих recording/keydown
closures с synthetic MediaRecorder. Покрыты порядок textarea→document, повторные
нажатия/IME, single-flight разрешения, ошибки constructor/start/permission,
отмена ожидающего старта, поздние события сломанной записи, stop после остановки
движка и предел пяти минут. Node suite 18/18; обе сборки app/remote проходят.
Реальный микрофон и системный permission dialog в этих проверках не открывались.

Большие universal uploads (20×50 МБ), мультимодальный batch, voice during tools,
Python migration и полная CI matrix — следующий отдельный блок. Текущий кандидат
не расширяет лимиты вложений и не меняет её автономию/ядро.
