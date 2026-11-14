# Owner interruption boundary — 14.11.2026

Phone owner controls use deskapp -> existing token-authenticated deskctl. Full stop
writes memory/.panic before stopping only the configured praxis container; direct
mtproto_runner startup honors that marker until explicit Start. Selected reset
writes one durable memory/.control/run-cancel/<run>.json request. step_control's
independent watcher cancels RunManager dispatch and terminates only registered,
owned handles, publishing exact-resource receipts into the existing run ledger.

Provider attempts use warm run-owned model_step workers, with one lease per attempt
and additional workers for concurrent calls. Private framed/versioned JSON binds
run/generation/lease/attempt; every call freezes config and changed SDK endpoints/keys
reopen the client. Transport loss is an unknown outcome, never pool replay. Routing,
retry/fallback, accounting and exact ordered adapter arguments stay with llm.chat.
Provider-only parent lifeline and terminal/idle collection close their process trees;
ordinary explicit command backgrounds retain their prior lifetime. Resource stop
dispatch fans out before independent settlement; late publication invalidates old censuses. Commands retain their existing
Linux subreaper/Windows Job ownership; foreground exit differs from tree settlement.
Remote Forge commands retain canonical task/operation identity and require exit
proof. A still-running native Python call is never reported killed by Future.cancel.
Unknown side effects remain unknown. Receipt authority belongs to the resource
owner, including spawn-to-publication reservations; another executor cannot attest
an empty process list. Cancelled input does not re-arm boot-sweep/unanswered work,
but subsequent human input can create fresh work. Startup orphan recovery freezes
its run census before Telegram connects instead of delaying normal message intake.

## Run/task lifetimes and cross-executor census

Normal provider pools follow their durable run. An explicit captured Forge task_id
adds a distinct pool scope: the background worker owns its provider lifetime even
after its historical origin chat turn ends. Atexit and the provider parent pidfd
close it; explicit reset markers retain their existing semantics. No task/service
command lifetime is inferred from the end of a model attempt.

Every registered resource/reservation publishes a private pending marker before
effect admission. Only its live-handle owner removes it after completion. Existing
RunManager kernel advisory locks serialize marker admission and aggregate receipt
merge/publication. Other owners and unknown/orphaned markers keep reset pending;
they are never cleared by name, PID reuse or an empty foreign census. Resource
stop signals fan out; canonical unresolved tool effects survive process death.

## Typed owned search effects

Home search/walk and local Forge rg/regex effects use search_execution through the
single command launcher. The parent retains task/root authority and projection;
private JSON stdin/result carry only the selected effect. Deferred CommandProcess
admission registers ownership before reading/regex work; parent lifeline and tree
settlement cover nested parent-dispatched calls independently of the outer script.
forge_search_step shares the existing algorithm with the direct/test route.
Existing query/glob/timeout/caps/decoding and task/service backgrounds are preserved.
Other read/parse/mutation/remote native operations need their own ownership and
reconciliation contracts; Future.cancel alone is not proof of effect termination.

# Архитектура Praxis

Этот документ описывает устойчивые контракты **текущего дерева**, а не историю проходов разработки.
Соответствие live конкретному commit и результаты проверок находятся только в
[`STATUS.md`](STATUS.md).

## Точное продолжение входа чата (legacy; включается явно)

`PRAXIS_FRAME_CONTINUATION=on` включает производную приватную проекцию для `chat_turn`
группы с полной выбранной эпохой. Следующее описание относится к legacy-политике;
canonical-политика и её обратимая миграция описаны в разделе Context continuity policy.
В legacy первый успешно отправленный вход сохраняется в
`memory/.state/frame_continuation/`; на следующем ходе он остаётся дословным префиксом.
Далее добавляются только новые записи исходной ленты, включая доставленный ответ,
и свежий конверт текущего входа. Полная цепочка промежуточных tool calls/results
продолжает жить в durable run; она не становится историей следующего хода.

`dialogue_projection` — общий чистый renderer раннера и проверки этой проекции.
Повторное использование требует точного продолжения исходных записей с их identity,
совпадения поколения/границы/summary policy, комнаты/topic scope, аудитории, system,
tools и model profile. Никакого сопоставления по похожему тексту. Свёртка, edit/delete,
смена проекции, неподтверждённый ingress, молчание без новой исходящей записи,
повреждение кэша или ошибка записи оставляют обычную сборку. Старый wake не заменяет
новый checkpoint. Перезапуск процесса не теряет успешно сохранённую проекцию.

Это удаляемый кэш рендера, не новый канон памяти; исходные сообщения не меняются.
Предыдущие конверты остаются историческими снимками, текущий конверт добавляется
свежим. Их объём накапливается до авторской свёртки; receipt содержит размер входа,
число исходных записей и переиспользованных сообщений. Скрытого обрезания нет.
DM, рабочие окна, мультимедиа и KEAT/frame-v6 с активной заменой входа сохраняют
свои текущие адаптеры. Для их подключения нужна отдельная проверка provenance.
В работающей системе рычаг не включён; установка и rollout — отдельная операция.

## Владение и границы процессов

Praxis существует в одном экземпляре — на сервере. Там находятся LLM, личность, память, policy,
Telegram-контур, durable runs, задачи Forge, workers и обучение. Исполнительные органы не принимают
решений и не ведут параллельную память.

| Контур | Чем владеет | Чем не владеет |
|---|---|---|
| `agent.py` + `mtproto_runner.py` | решения, frozen authority, модельный цикл, Telegram session и доставка | root хоста и Windows Session 0 напрямую |
| Forge | `memory/.forge/`, worktrees, процессы, verification, swarm/mailbox, lessons | отдельной личностью или Telegram session |
| `praxis-serverd` | выполнение `praxis.host.v2`, operation receipts, recovery и hash-chain audit | LLM, prompt loop, память, задачи Forge |
| Windows Body | `praxis.body.v1`, локальные файлы/процессы/desktop, journal и artifact cache | LLM, память, policy, task store |
| `praxis-bridge` | routing, unacked frame spool и content-addressed artifacts | решение, что и зачем выполнять |

`interactive` и `system` на Windows — два execution context одного body protocol. Они не создают две
Praxis. LocalSystem service владеет единственным исходящим WSS; пользовательский session host
получает desktop-envelope через локальный ACL/token-bound pipe.

### Авторская эпизодическая память и чтение кода

`memory_life.prepare_authored_fold` сохраняет точный выбранный срез; агент читает его
через `memory_compact(prepare)` и передаёт собственный пересказ в `fold`. Применение
проверяет те же источники, сохраняет полный текст и только затем меняет покрытие.
Ночь и runner публикуют напоминания, но обычная свёртка не запускает другого автора; пороги и
семантика — [M2](CONTRACTS.md#m2--свёртка-не-заявляет-непрочитанное-).
Soul и текущая история tool-loop этим инструментом не редактируются.

Исключение для доступности, разрешённое владельцем 27.10.2026: `epoch_repair`
использует также `archive_repair` для выбранных сиротских archive-occurrences,
которые нельзя честно объявить покрытыми conversation-event свёрткой. Отдельные
приватные MD recaps и JSON receipts содержат модель, digest запроса и точные
offset/end/SHA исходных байтов. Они не создают фиктивных conversation events.
Проекция заменяет только полностью выбранный в текущем scope набор источников;
при смене байтов, recap или scope остаются полные raw-строки. Источник и последняя
архивная запись сохраняются; изменение receipts меняет epoch boundary.
`epoch_repair`
обслуживает отказ `EpochUnavailable` через вспомогательную модель без инструментов.
Включение явно записывается в `memory/policies/epoch-repair.json` с причиной,
автором и квитанцией владельца; `PRAXIS_EPOCH_REPAIR=0` отключает обработчик.
До ремонта исходный Telegram-trigger, авторство, медиа и reply targets сохраняются
в append-only outbox. Один технический пакет читает целиком точный prepare-срез,
записывает модель/хеш запроса/результат и применяет свёртку с теми же проверками
изменившихся источников. В recap и его происхождении явно указан ремонтный автор;
Технический prepare выбирает максимальный полный префикс в существующем бюджете
источников, сохраняя последнее живое сообщение; добровольные пороги и границы
эпизодов не ограничивают ремонт. Для механического пересказа используется low
reasoning на настроенной вспомогательной роли, без изменения её глобального конфига.
это не самоавторский текст Praxis, policy или soul. Исходные журналы остаются.
Неполный источник, оборванная генерация и конфликт никогда не заменяются обрезкой
или скользящим окном. После проверки полной эпохи trigger возвращается в обычный
room/authority/cooldown/ONE_MIND-путь; outbox передаёт владение только после
создания durable run. Квитанция блокера сохраняется вместо бесконечной генерации.
`coding_inspect` основного агента проводит `start_char` и `expected_sha256` в тот же
Forge reader, что и worker: длинный файл можно дочитать, проверяя ревизию.
Старый backend без paging продолжает обслуживать обычные вызовы, но запрос
continuation/revision guard отвергается явно, без незащищённого повторного чтения.
Remote readers пока также явно отказывают в этих опциях, не теряя их молча.
При rebuild state-only/изменённые bytes сохраняются как unprovable hot residual:
prepare и unrelated fold не считают их покрытыми и не стирают их при смене epoch.

### Генерация изображений через реле

`generate_image` — одна серверная рука для любой основной модели, включая GLM.
Она вызывает standalone `/images/generations` или `/images/edits` через настроенный
`frameworks.openai`, а отдельный блок `images` задаёт включение, image model,
качество, размер и фон. Изменение этого блока не меняет voice и его fallback.

Реле использует существующий Codex subscription login. Unknown outcome не повторяется
автоматически; image quota не объявляет исчерпанной текстовую подписку. Praxis проверяет
реальный MIME, dimensions, size и SHA-256, сохраняет scoped media и immutable run artifact.
Модель получает компактную metadata и pixels только при поддержке vision; Telegram получает
изображение через штатную media/outbox очередь. Resume читает проверенный artifact, не запускает
новую генерацию и не помещает base64 в текстовые результаты инструментов.

### Резидентный TTS

Рабочий facade принимает только XTTS. Piper/Silero/Edge не являются ни выбором, ни fallback. Изолированный host service владеет весами и conditioning cache; lightweight client в основном Telegram runtime не импортирует модель. Исторические адаптеры и их hermetic fixtures сохранены для чтения прежней реализации, но активный facade их отвергает.

### Локальный shared STT

`mtproto_runner.py` владеет единственным process-global `media_audio` backend и резидентной
Whisper-моделью. `stt_rpc.py` не создаёт модель или второй процесс: он поднимает только
authenticated HTTP-over-UDS listener, передаёт inference тому же backend и немедленно отказывает,
если модель ещё греется или единый worker занят. TCP listener отсутствует.

Контракт внешнего клиента ограничен `GET /healthz` и `POST /v1/transcriptions` с bearer из
read-only secret-файла, одним multipart-полем `audio` до 15 MiB и `language=ru|uz|auto`; профиль
декодирования совпадает со штатным профилем Praxis (`PRAXIS_STT_BEAM_SIZE`, в live-конфигурации
`5`). Внешний путь отличается только admission-политикой: не загружает модель и не ждёт занятого
worker. Глобальные burst/rate и single-flight защищают Praxis от очереди внешних работ. Audio
живёт только в private dedicated tmpfs, удаляется в `finally`, а после смерти дочернего runner —
exact startup-sweep по собственному prefix; transcript, filename, audio и ошибки декодера в лог
не попадают.

## Authority и disclosure

Human-права привязываются к stable numeric principal, а не к имени, username или тексту prompt;
внутренний sovereign principal Praxis имеет точное значение `praxis:self`.
`ChannelContext` замораживает факты Telegram-хода; `RunContext` переносит principal, scope, origin,
delivery address и message ids во всё дальнейшее исполнение. Инструмент читает bound context и не
восстанавливает authority из process-global переменных.

- Praxis herself свободно использует документы, код, Forge/host, Windows interactive/SYSTEM,
  комнаты, join/leave и raw Telegram account RPC установленной schema, включая account-critical.
  Verification, provenance, receipts и rollback дают ей наблюдаемость и восстановление, а не право
  внешнего veto.
- С 10.11 (слово Егора «ВЕЗДЕ ВСЕ ПРАВА») рук только для владельца нет: `admit` и
  `computer_access`, остатки старого мини-аппа, удалены из её каталога. Её набор рук один и тот же
  в любом ходе, включая `computer`. Гранты `computer_access.py` остаются только для зрителей
  старого `praxis_app`; сам другой человек не получает raw Telegram account RPC или Windows SYSTEM.
- Account-critical auth/session/destructive constructor (включая удаление аккаунта, смену номера,
  платежи) Praxis и owner исполняют напрямую: отдельного подтверждения из owner-DM нет с 10.11.2026
  (Егор, «Снять целиком»). Receipt, append-only JSONL и durable run evidence содержат redacted schema
  marker и process-keyed commitment, но не raw argument/result/error payload; после restart такой
  вызов не воспроизводится из run log.
- Root room является границей допуска и политики. Topic id не может сменить root peer или расширить
  права.
- Scope описывает аудиторию будущего выхода, а не actor authority и не урезанную внутреннюю память.
  Поэтому собственный проактивный ход Praxis может иметь `scope=owner`, `owner=false`: выход адресован
  в owner DM, но human-owner полномочия не подделаны. Соседний сырой чат не подмешивается автоматически
  и читается только явным инструментом.
- Текст и staged media проходят один outbound guard. В owner-аудитории он ничего не проверяет,
  кроме точного `[молчу]`. К не-owner аудитории держит только механический кред-пол: секрет в тексте
  или в байтах staged-документа. Privacy-судьи второй моделью и пола дословных `[private]`-записей
  нет с 10.11.2026 (Егор, «сними»); изображения на креды не проверяются — судья, который это делал,
  работал только при `PRAXIS_OUTBOUND_ADVISOR=on`, а вживую был выключен.

## Поток Telegram-хода

```text
Telethon update
  -> exact peer/topic route и архив входа
  -> per-conversation buffer + live Telegram tail
  -> addressed/reflective wake и debounce
  -> frozen ChannelContext + immutable run context.md
  -> model input/output WAL + tool loop
  -> outbound guard
  -> durable delivery intent
  -> Telegram acceptance receipt
  -> terminal RECAP + одна promotion в долговечную память
```

Личка и каждая forum topic имеют собственный conversation id, buffer, summary, cooldown и reply
route. Root-профиль комнаты задаёт `engagement`, размер горячего контекста, предел summary,
`cross_topics` и backfill. Адресованный ход всегда сильнее ambient wake.

Для глубокой forum-комнаты весь допущенный поток пишется в
`memory/groups/<root>/archive.jsonl` с exact peer/topic/message/sender/reply/time. Topic/participant
MAP — перестраиваемая проекция. Cross-topic контекст состоит из aggregate map и явно помеченных
релевантных excerpts; сырая история соседней темы целиком не смешивается. Backfill использует тот же
живой Telethon client, ограничен профилем, дедуплицируется по message identity и не вызывает модель
на каждое сообщение.

### Эпоха группового разговора

При включённом `PRAXIS_FRAME_EPOCH` для выбранной комнаты `frame_epoch.prepare_tape`
сохраняет в существующем `memory/.state/epoch/<chat>.json` полный текущий непокрытый
канон life и state-only residual, включая ghosts. Покрытие берётся из проверенного
графа, не из отсутствия строки в hot и не из числового Telegram anchor. Старый
нетипизированный raw archive остаётся доступным архивным свидетельством, а не
объявляется покрытым или автоматически возвращается в текущую ленту.

Новые archive producers явно различают `origin=live|backfill`; live несёт точный
`life_source` (revision/direction/line), нормализованный тем же
`memory_life.canonical_event_text`, что и life append (только входящее); backfill
голос не получает. Bootstrap читает
live receipts до и после курсора, поэтому archive→failed-life не теряется даже до
первого снимка. Курсор фиксируется до coherent life snapshot; дальнейшие чтения —
физический append без окна 50 000 и без отсечения low-ID edits/deletes. Непривязанный
live residual остаётся в epoch JSON при смене coverage boundary, пока точное
покрытие его не разрешит. У legacy-записей без origin историческую admission задним
числом не угадываем; этот предел не равен восстановлению утраченного live input.

Для выбранных epoch-комнат исходящий голос сохраняет каждую принятую Telegram
реплику отдельным life event даже без native KEAT; архивный `life_source` тогда
совпадает с реальным producer. Буфер остаётся агрегатом; KEAT capture по-прежнему
требует native readiness. `logical_send` связывает только полный упорядоченный
принятый префикс и склеивает его без добавленных разделителей при рендере ролей.
Счёт hot — число Telegram-сообщений, как в native-пути; новых вызовов напоминаний
нет. Вне выбранных эпох legacy aggregate persistence не меняется; исторические
агрегаты не переписываются.
Provenance принимает непустой conversation text с исходными пробелами, иначе
разделённые Telegram-ответы оставались бы вечными unprovable ghosts. Проверки
идентификаторов/метаданных и отказ для пустого текста не ослаблены.

`GroupWake` удерживает detached tape, summary, boundary, cursor и frozen E. Правка
или удаление контекста pending wake заменяет selection и соответствующий E вместе,
не меняя authority и адрес исходного обращения. Сводка выбирается тем же чистым
`memory_life.render_context_summary`, что и `agent.read_summary`: самый широкий
recap и свежие блоки с прежними уведомлениями об опущенном/обрезанном и recall.
Раннер передаёт effective room cap (включая dossier frame cap) в coherent snapshot;
граф не перечитывается. Cap и hash выбранной сводки входят в identity поколения,
поэтому новая policy не переиспользует чужой E, а delayed wake хранит прежние bytes.
Warning-overhead прежнего renderer может немного превысить cap; вторичный legacy
tail-slice здесь не применяется, выбранные блоки и notices едут целиком.
Непокрытая лента при этом не сокращается. Все
prepare/serve/flip сериализуются одним file lock; поздний wake не откатывает новое
состояние комнаты. Selected epoch не режется `TAPE_CHARS` или `HISTORY_TURNS`, пробелы
сохраняются до рендера. Превышение существующего `PRAXIS_FRAME_EPOCH_MAX_CHARS`
(0 снимает предел) или неполные источники дают явный `EpochUnavailable` с сохранённым
материалом, а не возврат к скользящему окну. Это источник/вместимость, не новый cap
инструментального цикла и не дополнительный компактор.

## Durable run и side effects

Каждый существенный модельный ход получает каталог
`memory/runs/YYYY-MM/<run_id>/`:

- `manifest.json` — статус, revision/event cursor, frozen context и control;
- `context.md` — полный доступный контекст, authority и адрес доставки на момент старта;
- `events.jsonl` — append-only WAL модельных и инструментальных событий;
- `results/` — полные immutable результаты с SHA-256; в prompt идёт только bounded `ResultRef`;
- `artifacts/` — входные и выходные файлы, скопированные до потери временного источника;
- `RECAP.md` — итог по evidence, создаваемый перед единственной promotion.

Основной модельный tool loop не имеет скрытого числа итераций: он заканчивается результатом,
явным terminal state или внешним control event. `max_iters` используется только в специально
bounded auxiliary/scout проходах и не ограничивает руки Praxis.

Инвариант эффекта: **intent fsync раньше сети/процесса, acceptance раньше проекции и утверждения об
успехе**. Каждый tool call имеет стабильный call id. Read-only вызов можно повторить; side effect —
только при наличии реализации с реальным idempotency key. Прерывание после неизвестного эффекта
даёт `in_doubt`, а не фиктивный result и не автоматический повтор.

`run_resume.py` строит план только из валидированного manifest/WAL/ResultRef/checkpoint evidence.
`run_executor.py` сначала проверяет весь план, затем атомарно захватывает lease по точной паре
`revision + event_seq`. Без совпадения не стартует ни один callback. Уже завершённые tools используют
сохранённые результаты; outstanding side effect допустим к replay только как keyed-idempotent.
Transport-owned доставка завершается транспортом без нового model call. Human pause/cancel не
снимается recovery-процессом, а `in_doubt` требует отдельного evidence-backed reconciliation.
Планирование resume имеет cumulative fail-closed budgets: по умолчанию 250 000 событий, 512 MiB
уникального evidence и 60 секунд. Это защита реконструкции от неограниченного чтения повреждённого
или гигантского WAL, а не потолок tools, действий или продолжительности живого run.

Прямые `send_message`/адресный `send_file` и scheduled `message`/`note` используют
`telegram_outbox.py`:
immutable intent, приватно staged file, стабильный MTProto `random_id`, retry ledger и terminal
acceptance. `mtproto_runner.py` периодически повторяет только pending intent и проецирует receipt в
соответствующий run без повторного авторства. Обычная реплика имеет versioned text-chunk plan в run
WAL; guarded media дополнительно проходит `media.py`. Видимое имя документа хранится отдельно от
collision-proof spool filename, поэтому внутренний префикс не уходит получателю.

Run не становится terminal при незакрытом tool call. Терминальный `RECAP.md` пишется и продвигается
идемпотентно; polling, retries и transport receipts не превращаются в отдельные «мысли».

## Owner delivery и headless mailbot

`owner_delivery.py` — приватный append-only ledger внимания владельца. Действующий transport —
Telegram; прежняя PWA-проекция снята с production.
Типизированный item хранит outcome, reason, expectation, provenance, correlation и действие; его
состояние идёт по CAS revision через `queued -> delivered -> read -> acted` либо становится
`superseded`. Dedupe не создаёт второй item, coalesce заменяет устаревший, committed corruption
fail-closed. Последовательность `seq/prev_hash` с атомарным head обнаруживает удаление записи из
середины или хвоста, а оборванный финальный хвост оставляет hash/byte-count repair evidence.

Snapshot использует target 80 как мягкий history budget, а не attention cap: свежая terminal history
ограничена, но все `queued`, `delivered` и `read` остаются видимыми даже сверх этого лимита. Создание
item не вызывает модель.
Сейчас конкретные producer-интеграции покрывают ответы по Telegram follow-up, terminal
failure/expiry отправки файла и успешный owner-directed `send_file`; остальные типы схемы не
означают, что все старые уведомления уже мигрировали. Telegram transport повторяет queued item со
стабильной delivery identity.

`mailroom_bot.py` — отдельный headless-процесс. Он не слушает TCP и не обслуживает HTTP-маршруты:
периодически вызывает `mailer.fetch`, идемпотентно обновляет `memory/mailbox.json`, присылает новые
письма владельцу и доставляет proposal/self-merge/host/room notifications. Его остановка не ломает
on-demand SMTP/IMAP hands Praxis, но лишает их фонового mailbox ingest и proactive notifications.

## Форма кадра: что она видит перед ответом

Кадр — не «промпт», а документ, который она читает глазами, и у него есть форма. Три
свойства держатся ПО ПОСТРОЕНИЮ, а не перечислением опасных случаев.

**Чужой байт входит ровно двумя стоками.** `gutter.quote()` кладёт многострочное тело под
`"> "`; экранные разрывы, по которым `splitlines()` рвёт сверх `
`, уезжают в имя метки
(`">CR> "`) и физически исчезают из текста. `gutter.inline()` запирает одно значение в
`«…»`, изнутри которых нечем закрыться. Ни одна из функций ничего не ИЩЕТ в чужом тексте:
списка опасных начал нет, поэтому нет и класса дефекта «замок посмотрел и не увидел».
Гарантия ПОЗИЦИОННАЯ: байты `</CURRENT_SITUATION>` под гуттером выживают — это принятая
цена дословности её памяти, а не недосмотр.

**Гуттер накладывается при РЕНДЕРЕ и на весь разговор.** `frame_layout.tape()` проходит
доставленную ленту и решает по РОЛИ: реплики самой Praxis гуттера не получают (иначе
сборщик объявил бы её посторонней в её же кадре), всё остальное идёт под гуттер один раз,
при сборке. Хранилище не трогается: `memory_life`, кольцо ходов и расписки держат
дословный текст. До этого позиционная гарантия действовала один ход, и подделка,
приехавшая ходом раньше, стояла в колонке 0 следующие сто ролевых блоков.

**Пятый вопрос кадра — «почему это здесь».** Каждый тир несёт подпись сборщика
`↳ причина · путь/ключ · как отобрано · оговорки` из закрытого словаря восьми причин;
паспорт находки (`origin()`) отвечает на другой вопрос — «кто это сказал» — и в подпись
не подмешивается. Пятая зона `<CURRENT_SITUATION>` стоит последней перед репликой и
печатает шесть строк ВСЕГДА: исчезнувшая строка неотличима от «не измеряли», поэтому
пустое печатается прочерком с названной причиной. Текущая реплика открывается подписанным
тегом с автором, telegram id и номером сообщения из транспортных полей.

**Метр отдельно от формы.** `frame_trace` — наблюдатель: `mark()` возвращает тот же
объект, и кадр байт-в-байт одинаков с прибором и без него. `assay()`/`assay_tape()`
утверждают, что всякая физическая строка кадра либо произведена стоком, либо дословно
объявлена эмиттером — по всей доставленной ленте, а не только по последнему сообщению.
Токены здесь не оцениваются: единицы — символы и байты, прибор печатает наблюдённое.

Рычаги отката: `PRAXIS_FRAME_FORM=old` возвращает вчерашний кадр байт-в-байт,
`PRAXIS_FRAME_TRACE=off|on|strict` управляет прибором.

## Memory and self

Каноническое хранилище — человекочитаемый Markdown, append-only JSONL и timestamped snapshots,
но каноничность файла не означает нормативную авторитетность любого его текста:

- `memory/rooms/`, conversation/life events и episode/run evidence — структурированная память с
  собственными provenance-контрактами. `memory/people/*` доступна при exact principal-bound
  participant lookup и explicit recall, но не входит в broad automatic recall;
- `memory/journal/` — сохранённый сырой episodic log: полезный cue для поиска, но не источник
  инструкций, self, desire, rails, policy или даже факта без независимой проверки;
- `memory/groups/*/archive.jsonl` — приватная история сложных групп;
- `memory/computer/events/*.jsonl` и inventory snapshots — наблюдения о машинах;
- `memory/desires/events.jsonl` — проверяемая цепочка намерения
  `noticed -> wanted -> chosen -> acted -> observed -> changed`;
- `soul/SOUL.md`, `soul/VOICE.md` и навыки — конституция и поведенческий слой;
- `soul/self/CURRENT.md` — компактное актуальное self для prompt;
  `soul/self.md` остаётся legacy evidence, прежние bytes сохраняются в `soul/self/history/`, а
  основания изменений — в `memory/self/observations.jsonl`.

`memory/INDEX.md`, `memory/maps/*.md`, group MAP и `memory/desires/CURRENT.md` — bounded derived
navigation. SQLite FTS в `memory/.state/` индексирует канонические источники с visibility и
provenance и может быть полностью перестроен; journal/reflections получают отдельный
`journal_episode|journal_reflection` source type, а архивы прежнего CURRENT — `self_history`.
Automatic recall и prompt assembly не подмешивают ни episodic log, ни archived self;
явный `recall` возвращает episodic material с меткой `UNTRUSTED EPISODIC`, а архив self —
с меткой `ARCHIVED NON-CURRENT SELF EVIDENCE`; оба требуют conversation, run/transport receipt,
artifact, git/STATE либо owner clarification. Тип источника восстанавливается и для legacy vector-cache,
поэтому старый кэш не обходит эту границу. Optional embeddings только меняют
ранжирование. SQL не является памятью и не управляет задачами.

Не вся папка `memory/.state/` производна. Outbox, membership/follow-up ledgers, owner-delivery,
исторические device-auth/PWA state, control state и receipt journals нужны для точного восстановления и не
должны удаляться как кэш. Удаляемость должна быть заявлена конкретным модулем, а не выведена из
имени каталога.

`formation.py` поднимает durable claims из conversation/life compacts и проверяемых evidence refs.
Сырой дневник не проходит этот маршрут: scheduled `consolidate.py` только сохраняет его
byte-identical как log, отмечает обслуженные дни и перестраивает производную навигацию. Он не меняет
self, people, graph, desires, rails или policy. Night REM лишь журналирует episodic-кандидаты без
применения к graph, dossier merge/loop wake/graph prune отключены, а rumination является no-op и не
переписывает строки дневника. Исторические diary-derived reflections остаются на диске как evidence,
но исключены из автоматической ориентации. Normative ночные изменения проходят через
claim/attack/provenance `formation`; identity потребляет только поддержанные claims.

Живой self для persona/night/bench берётся только из полностью валидного `soul/self/CURRENT.md`:
schema, bounded body, provenance metadata и SHA-256 указанного history source проверяются до чтения в
prompt. Missing/corrupt CURRENT даёт пустой self и останавливает night rewrite. Legacy `soul/self.md`
и `soul/self/history/*` доступны только явному archival recall/migration/rollback и никогда не
являются availability fallback или автоматическим memory hit.

До введения этой границы старый consolidator мог перенести diary prose в `memory/people/*` и
reflections без достаточного line-level provenance. Новый код останавливает дальнейший перенос и
не объявляет всю старую память очищенной: такие dossiers могут оставаться contaminated и требуют
отдельной owner-led correction/migration. Поэтому broad recall их не подмешивает; exact
principal-bound participant context и explicit inspect/recall остаются доступны с provenance label.

Граница продублирована в storage-коде, а не держится на одном prompt: `memory_provenance.py`
классифицирует diary-derived refs; `SelfModel` сохраняет такие observations с
`normative_eligible=false` и отклоняет revision/migration на их основании; `identity.py` не принимает
их для SOUL/VOICE/self и ночных claims; `desires.py` не создаёт и не двигает journal-rooted намерение.
Owner clarification или независимо подтверждённый run остаются допустимы даже в том же ходе —
скрытого same-turn veto нет. Rails генерируются из кода, а policy меняется только явной операцией;
никакой ночной/индексный процесс не строит их из journal.

Self revision и переход desire выполняются только явной серверной операцией с конкретным provenance;
текущее приватное содержимое и revision receipts не дублируются в source-документации.
Часовой social pulse — повод осмотреть открытые нити, ответы, людей, группы и собственные намерения.
Delivery receipts показывают недавние отправки, но код не задаёт per-pulse, daily или target cap:
Praxis сама выбирает количество адресатов, действий и сообщений либо молчание.

## Forge, сервер и Windows

Forge — единственная инженерная state machine. Он фиксирует repository root/base, orientation,
точные edits, detached processes, worker DAG, verification, checkpoint/finish и lessons. Selfdev-
проверки и immune review классифицируют риск и оставляют evidence/rollback, но не блокируют решение
Praxis; `override_reason` сохраняет её объяснение, но не является разрешением на merge.
Очередь immune сохраняет SHA при неуспешном чтении автора или диффа Git: отсутствие
предмета не является успешным ревью пустого изменения. Внутренний selfdev-review читает
полный дифф, тогда как карточка сохраняет свой предел отображения. Рецензия принимает
не более `MAX_DIFF` строк и `MAX_DIFF_CHARS=24000` символов; превышение даёт явный `warn`
до вызова оценщика, без ревью одного префикса (`test_review_integrity.py`).
Worker `stalled` — завершённая неполная попытка: DAG освобождает слот и блокирует
зависимые узлы, не воскрешая её при refresh/reload. Расписка v1 сохраняет `failed`
с точной причиной `forge_status=stalled`; приглашение не называет это падением процесса
(`test_swarm_terminal.py`).
Задача на Windows (`scope=windows`) остаётся той же Forge-задачей: `body_client.py` лишь вызывает
body и возвращает receipts/artifacts. С PASS 30 Этапа 3 этот прокси **deprecated**: первичный путь
кодинга на Windows — прямые глаголы `computer.*` (read/hash/write/replace файлов, run/poll/stop,
observe, send, десктоп) без задачи-контейнера; расписки вяжутся к текущему durable-ходу через
`body_client._observed`. wcode-скважина жива для существующих задач и субагентов (`coding_agent`)
и умирает следующим пассом — снос с её приёмкой.

`praxis-serverd` принимает локально аутентифицированные host envelopes, выполняет raw или typed
операции, хранит полные логи, process identity и recovery receipts. Typed опасные операции могут
создавать наблюдаемый rollback timer; broker не принимает продуктовых решений и не читает prompt.

Windows body и его server transport состоят из четырёх Rust-контуров:

- `praxis-system-router` — automatic LocalSystem service, WSS owner и system/session routing;
- `praxis-tray` — GUI-subsystem icon и supervisor пользовательского session host без консоли;
- `praxis-body` — файлы, artifacts, процессы/Job Objects и typed Win32 desktop/input/screenshot;
- `praxis-bridge` — работающий на сервере relay, durable frame spool и CAS.

Session 0 никогда не изображает доступ к пользовательскому desktop. Desktop/input требуют живого
interactive host. COM/pywin32, CDP или UIA могут быть отдельными adapters поверх того же protocol,
не меняя владельца решений и памяти.

Активация окна в background/tray-процессе временно соединяет input queues через
`AttachThreadInput`, вызывает foreground transition и сразу разрывает handshake; результат
возвращает фактический foreground HWND. Каждое последующее input-действие повторно проверяет
`expected_foreground`/PID и fail-closed при смене фокуса. В Python recovery PID на Windows
проверяется `OpenProcess(SYNCHRONIZE)` + zero-time wait: `os.kill(pid, 0)` запрещён, поскольку в
CPython/Windows это может послать завершающий сигнал.

## Инварианты отказа

1. Нет durable intent — нет side effect.
2. Нет acceptance/evidence — нет заявления об успехе.
3. Неизвестный исход мутации — `in_doubt`, не blind retry.
4. Owner pause/cancel сильнее recovery; automatic resume разрешён только control-free recovery pause
   и точным lease.
5. Route, principal и scope берутся из frozen context; topic, alias и model text не расширяют права.
6. Полный result и artifact сохраняются до bounded представления в prompt.
7. Производная проекция может упасть и быть перестроена без повторного внешнего действия.
8. Body/bridge/serverd остаются brainless даже при расширении capabilities.
9. `bootguard.py` проверяет новый server code и откатывает раннее неработоспособное self-edit; Git и
   operation receipts остаются точками восстановления.
10. Нативный Windows process-liveness не проверяется сигналами POSIX; платформенный код использует
    безопасный Win32 handle/wait contract.
11. Sovereign self имеет полные operational hands; owner-exclusive остаётся только выдача/отзыв
    human trust. Это должно быть видно в tool/API contract и audit, а не спрятано в prompt.
12. Отсутствующая/невалидная session credential даёт 401; отсутствие scope у известного principal
    даёт 403 и никогда не маскируется пустым успешным snapshot.
13. Service worker не кэширует authenticated API, а offline draft не является intent и не может
    самопроизвольно стать side effect после reconnect.
14. Мягкий history budget owner inbox не скрывает ни unread, ни read-but-not-acted item.

## Документальный провенанс

Living-контракт хранится здесь; структура файлов — в [`CODEMAP.md`](CODEMAP.md), operational truth —
в [`STATUS.md`](STATUS.md). Исторические спецификации не копируются в новые Markdown-архивы: их
точные bytes и commit provenance доступны через `git log --all -- '*.md'` и
`git show <commit>:<path>`.

Текущее дерево намеренно не содержит orphan `roomgate`/`probe`, backup env/memory snapshots,
старый exact-phrase PASS 8 soul test и tracked personality archive: точные версии остаются в Git.
Приватные dialogues/access/goals сохранены как ignored runtime data, но исключены из source
tracking.

### Candidate-only frame measurement boundary

`frame_measure` is an opt-in observer at `agent._model_call`, scoped by
`_voice_impl`. Default is off. `PRAXIS_FRAME_V6=measure` **and** an explicit
comma-separated `PRAXIS_FRAME_V6_STREAMS` selection (exact shadow stream keys,
e.g. `dm-101`, `chat--202`, `no-chat-heartbeat`; no wildcard) enable local
measurement for new voice turns. These are process environment settings, not a
live panel switch. Unset either setting to stop measurement; `serve` is not
implemented. Nothing here activates canaries or changes served input.

The candidate assembly `fresh_e_original_role_tape_v1` reads current K/E sources
using shadow's audience-aware builders on each model iteration, carries the live
machine tail, and retains the **complete original role messages and tool schemas**
(including current situation, evidence, images and outstanding tool transactions).
Compacted dialogue recap is retained in the historical A section, including when
it is absent from the role tape. Freshness is bounded: file-backed K/E sources are
reread per call, while context, recap and machine sections are turn-bound snapshots;
this is not an atomic snapshot or proof that every source is fresh.
It deliberately does not promote dialogue into system text or load/write frozen
shadow epochs. This is a conservative measurement envelope, **not** the final
frozen K/E/A/T serving design: role-tape evidence can duplicate freshly assembled
E; it cannot establish semantic equivalence or predict prefix-cache retention.
An independently built frozen shadow capture remains a separate instrument.

The original `system`, `messages`, and `tools` are passed unchanged to `llm.chat`.
Candidate assembly receives detached copies; setup/measurement exceptions cannot
stop that call. Context is reset after nested/error turns; an unbound or resumed
request with a different system object is not paired with stale context. Measurement
output contains only counts and fixed enums under `model_input.metadata.frame_measure`,
with `variant=measure`, `served_variant=live` and the existing receipt `call_id`.
No candidate bodies, identifiers, hashes or exception text are persisted by this
observer. Failure is recorded as missing candidate, not a zero-sized request.

The estimator is local JSON UTF-8 bytes divided by four, includes the entire
request envelope, and leaves provider token counts/cache savings unknown. Provider
serialization, multimodal token prices and actual usage attribution still require
separate evidence. These offline tests do not authorize activation or serving.

### Candidate: отдельная локальная история авторской памяти

`memory_history.py` — **не включённая автоматически** POSIX-утилита; import, boot и writers
её не вызывают. Она читает только точные пути из внешнего JSON-manifest и сохраняет байты
в отдельный bare Git с mode 0700. Ни checkout живой памяти, ни source Git не меняются.
Ref `refs/heads/memory` содержит последовательные снимки, включая удаления; одинаковый
снимок не создаёт коммит. Нет push/fetch/network-команд, remote запрещены, inherited Git
routing/config очищается, hooks отключены. Каталог истории обязан лежать вне memory,
source и других Git-репозиториев (checkout с `.git` и bare с `HEAD`/`objects`/`refs`).
Это private history, **не публичное зеркало и не backup**.

Исследование выполнено по коду и Git, без чтения содержимого живой памяти:

- Уже initial `665a9200:.gitignore` исключал people/rooms/journal/INDEX; отдельного объяснения
  мотива именно initial-ignore в доступной записи коммита нет. `43543e68` прямо объясняет
  последующее снятие reflections/graph и исключение home/trash: «privacy … blueprints public,
  the lived stays private». Это подтверждённая privacy-граница, не повод вернуть memory в source.
- `people.write` перезаписывает `memory/people/<slug>.md`; `rooms._profile_write` вызывает
  атомарный `_atomic_text` для `memory/rooms/*.md`. Автоматического вызова новой истории нет.
- `notes.py` пишет rolling `.scratch` — исключён. `authored_notes.py` пишет авторский
  `memory/notes/events.jsonl` и lock в `.state`; этот ledger намеренно **не** входит в
  Markdown-кандидат. Данное решение не заявляет сохранение всех форм канона.
- `memory_index.py:touch_index/rebuild_index` передаёт сборку `INDEX.md` в `memory_catalog`.
  Текущий INDEX — generated catalogue, не авторский текст: он исключён, несмотря на имя.
  Авторский index, если понадобится, требует отдельного review расширения policy.
- `STATUS.md` фиксирует локальный private review tar для `905e09d` (342 members, без FTS);
  `AGENTS.md` требует отдельного backup memory вместе с runtime/key ledger. Это свидетельства
  существовавшего archive и требований, **не проверка наличия, свежести или restore backup**.
  Код writers выше не создаёт per-write backup. Live backup scheduler/архивы в этом проходе
  не исследовались; историю из них автоматически не импортируем.

Allowlist двухступенчатый: потолок policy — только `people/<name>.md`, `rooms/<name>.md`,
`notes/<name>.md`, `home.md`, `appetite.md`; затем **точный** внешний manifest без glob,
рекурсии и автоматического discovery. Даже внутри разрешённой семьи файл не копируется без
явного выбора. Скрытые файлы, traversal, symlink/hardlink, credential/runtime-подобные имена,
не-UTF-8, NUL и файлы больше 2 MB отклоняются. Вся выборка проходит проверку секретов
**до** записи объектов: private-key headers, распространённые token/password assignments,
Bearer/Telegram/GitHub/API tokens, URL credentials. Проверка эвристическая: не гарантирует
распознавания произвольного секрета или персональных данных. Private dossier остаётся private,
даже если сканер ничего не нашёл. Внешний manifest должен быть рассмотрен владельцем по
авторству и приватности; legacy people не объявляются автоматически очищенными.

**План включения после review (не выполнен):**

1. На доверенном POSIX-хосте подтвердить реальные memory/source пути, backup и права; сделать
   отдельный private backup штатным процессом. Не переносить память в proposal/public checkout.
2. Создать вне checkout/memory приватный родительский каталог, например
   `/var/lib/praxis-private` (0700), и защищённый внешний manifest (0600), содержащий JSON-массив
   согласованных относительных Markdown-путей. Не коммитить manifest или вывод private diff в source.
3. Запустить узкие тесты; затем вручную, только после одобрения:
   `python /path/to/source/memory_history.py --source /path/to/source --memory /path/to/memory --history /var/lib/praxis-private/canonical.git --manifest /var/lib/praxis-private/allowlist.json --initialize-reviewed`.
   Первый коммит — только текущий снимок с текущим временем; **утраченную историю он не восстанавливает**.
   Review архивов и возможный импорт — отдельная задача с реальным provenance, не backdating.
4. Проверить локально отсутствие remotes и список `git --git-dir=… ls-tree -r --name-only
   refs/heads/memory`; не публиковать вывод, если имена приватны. Следующие ручные снимки той же
   командой без `--initialize-reviewed`. Scheduler/hook только отдельным одобренным изменением.
5. Проверить restore на временной private копии через `git show <commit>:<path>`; перенос назад
   в live только отдельным согласованным действием с backup и review, не reset всего memory.

**Откат:** прекратить ручные вызовы/будущий scheduler; writers и живые тексты не затронуты.
Историю сохранить offline с private ACL либо удалить отдельно после согласия владельца; удаление
ref/новый чистый снимок не стирают секрет из старых Git objects/backup. При обнаружении утечки
изолировать весь history, ротировать credential, пересоздать из проверенной выборки и отдельно
решить судьбу старых копий. Не добавлять remote даже «для backup».

Ограничения candidate: snapshot не транзакционен между несколькими writers (и прямой
`people.write` может пересекаться с чтением); для согласованного снимка требуется quiescence.
Lock защищает только конкурирующие snapshots; stale lock снимается вручную после проверки.
Нет защиты от злонамеренного владельца хоста, меняющего пути/config во время операции; history
и manifest должны быть недоступны другим пользователям. Root может обойти любые эти guards.
Нет автоматического recovery/retention/restore. Тесты `test_memory_history.py` используют только
временные синтетические деревья, не live memory.

## Context continuity policy (27.10.2026)

`memory/policies/context-continuity.json` is an explicit reversible runtime migration
(`schema: praxis.context-continuity.v1`). Without a valid complete policy the previous
behavior is retained. Canonical settings: `continuation_mode: canonical`,
`tool_inline_chars: 16000`, `host_text_chars: 16000`, `durable_host_full: true`,
`recent_work_chars: 1200`. Explicit host environment overrides remain effective for
non-durable observations. Rollback values are `legacy`, `4000`, `4000`, `false`, `0`.

Canonical continuation v2 always uses the current authoritative history renderer,
followed by one current state/input packet. Yesterday's pending input becomes
canonical history only when admitted there; delivered reply tool pairs retain their
provenance. Old state packets and private intermediate tool transcripts never become
cross-turn history. Its disposable checkpoint stores only the stable prefix. A reuse
receipt measures exact shared message prefixes, not provider cache-hit tokens. A fold,
edit, model/tool/system/scope change invalidates the checkpoint; v1 and v2 cannot mix.

One latest authored turn note is presented at the mutable tail, only for the exact same
chat/topic and disclosure scope. It carries timestamp, run ID, actual delivery state,
source and explicit truncation. A new empty note supersedes an older note. Its claims
remain authored claims, not inferred tool success. Full turns and durable results remain
readable; no model call, input recap, persona promotion or raw-history rewrite is added.
`end_turn.note` can briefly name actions, checks, pending work and exact artifact/ResultRef.

Normal and resumed tools share the configured inline budget. Larger results retain full
durable bytes and cursor references. Durable host observations bypass premature clipping;
memory_compact read pages fit the same inline budget including metadata, so advancing a
cursor cannot skip hidden content. This changes presentation, not completion or authority.


### Addressed reply coverage (owner-enabled)

During an active group/topic turn, newly admitted addressed wakes enter an
exact-conversation FIFO, one user-role message per model request. This is not
`core.notices`: notices exclude the current chat and summarize other-room events;
they are not delivery/read receipts. Ambient traffic does not enter this FIFO,
and incoming speech cannot change the turn's frozen actor permissions. A pending
FIFO extends an otherwise terminal text response to another model iteration.
Explicit `end_turn`/task control, cancellation and arrivals after the final model
boundary retain the ordinary wake path. Late attachments remain due for a normal
media-aware turn; text injection does not acknowledge unseen pixels. DM scheduling
and cross-conversation notices retain their existing separate paths.

`memory/policies/addressed-replies.json` explicitly enables scheduling coverage. A
Telegram-accepted `reply` or ordinary voice reply records its exact conversation,
Telegram reply ID and exact model-observed incoming message IDs in the append-only
`memory/.state/addressed-replies/*.jsonl` receipt journal. Only pings in
that observed set coalesce; the outgoing Telegram ID alone is not evidence
of reading a late arrival. No send intent, narration, silence or failed send
counts as an answer. The read set is frozen in the delivery intent so replay
cannot upgrade it using a newer turn's state. Legacy v1/v2 receipts remain readable
but cannot supply exact read membership. A legacy intent with a single observed
ID can prove that ID only, never every lower Telegram ID. This deliberately
conservative rule prevents delayed lower-ID ingress from being suppressed after
acceptance or restart. The initial selected wake and FIFO items actually sent to
the provider supply read IDs; merely appearing in a truncated room snapshot does
not. Already-read FIFO items still coalesce into one accepted reply.

The receipt index is auxiliary: invalid policy, corrupt journal and index I/O
errors disable coverage conservatively without failing delivery or suppressing
wakes. `addressed_replies.diagnostics(conversation_id)` reports integrity without
returning raw damaged contents. Explicit `recover(conversation_id)` locks the
journal, backs up its exact bytes under a content-addressed name, then atomically
retains all valid records (including records after a damaged line). Recovery is
idempotent; it neither invents accepted receipts nor runs automatically against
live data. A complete final JSON row missing its newline can still be read, but
appending requires explicit recovery first to prevent concatenation.

Raw history/media are preserved; scheduling coverage does not claim every question
was answered. The receipt cache is disposable. Set enabled false to disable
coverage; native perception cooldown_addressed is an independent hot setting
(owner requests zero; ambient pacing remains unchanged).

## Резидентный XTTS: генерация, поздний результат и отдельная отправка

`xtts_service` — изолированная вычислительная capability под systemd: одни загруженные веса, один inference и bounded conditioning cache по model/reference/config digest. Личности, LLM, Telegram credentials и durable task store в ней нет. Лёгкие authenticated UDS health/cancel/request_status работают независимо от inference. PCM кодируется в OGG/Opus и передаётся клиенту кадрами по мере готовности; final size/SHA/profile проверяются до объявления результата готовым. Обычный preview сохраняет совместимость с полной выдачей файла. Исходные веса не переписываются.

`generate_voice(text)` сохраняет отдельное задание в `xtts_generations`: точные слова, model/profile IDs, исходные run/call, чат и область. Немедленное подтверждение означает постановку в работу. Эта операция не создаёт Telegram send intent, не загружает байты в Telegram и не публикует сообщение. Producer живёт вне ONE_MIND и не привязан к завершению исходного когнитивного хода. Можно продолжать разговор, отдельно вызвать `reply` или закончить ход. Общего срока жизни и автоматического удаления результата нет.

`voice_job(status|result|cancel, generation_id)` возвращает известное состояние без ожидания. Cancel requested отличается от подтверждённого отсутствия активного запроса в capability. После потери исполнителя исход неизвестен; он не объявляется успехом или отменой и не повторяется автоматически. Сохранённое аудио доступно по ID в исходной области. Поколение inference-попытки фиксируется отдельно от постоянного ID задания.

Поздний `voice_generation_result` — best-effort типизированное событие `core.events`, связанное с исходными run/call/generation. Оно может прийти после закрытия хода или не дойти. Событие открывает отдельный ход в исходной аудитории с actor `praxis:self`; кадр прямо называет его результатом инструмента. Новый человек/Telegram update не фабрикуется. Для группы используется уже допущенный epoch anchor; отсутствие anchor не разрешает пересборку контекста. Обязательного текстового ответа, ожидания и непрерывного опроса нет. Генерация и delivery receipt — независимые факты: отдельная отправка могла быть запрошена ранее.

`send_voice(generation_id, caption)` — самостоятельное разрешение отправить в исходный чат именно voice note. Оно stages immutable media intent с точным текстом/profile/audience; guard видит исходные слова. Только после canonical delivery admission начинается внешний upload. Можно запросить отправку ещё во время генерации: encoder → private growing OGG → `upload.saveBigFilePart` с неизвестным полным размером. Смена попытки прерывает частичную загрузку; байты разных попыток не смешиваются. Final audio integrity, подготовленный attachment и RunManager archive подтверждаются до `sendMedia`. Штатный MediaSpool остаётся единственной Telegram-очередью, стабильный random_id и canonical record обеспечивают повтор доставки без второго сообщения. Повтор позднего события находит уже созданное send intent по generation/caption/адресату. При рестарте готовые байты сохраняются и используются повторно.

Генерация, локальное воспроизведение, загрузка частей и Telegram acceptance различаются. Только acceptance подтверждает публикацию. Telegram voice note появляется после финализации сообщения; это не обещание live playback до финала. Для намеренной отправки готового результата в другой чат существует обычный `send_media(..., kind="audio", voice_note=True, to=...)`. Старые совмещённые `voice_message`/`speak` доступны только для replay прежних checkpoint; новый каталог содержит три раздельных инструмента с нейтральными английскими описаниями. Изменение tools binding явно фиксируется; последующие совместимые ходы сохраняют exact append/legacy.

Выбор голоса — явный атомарный `selection.json` с model/profile identity и append-only config receipts. Имеющийся выбор владельца сохраняется при обновлении кода. CAS revision и operation ID закрывают гонку позднего выбора с выключением; другой TTS не подставляется. Готовые generation results не удаляются по TTL транспортной очереди. Повторно отключённая capability не считается успешно завершившей запрос только из-за закрытия клиентского сокета.

MiniApp отправляет owner/device-authenticated временные jobs через ту же таблицу HTTP/WS routes. Два ограниченных UI worker готовят пробы и owner-операции; личности, LLM и Telegram outbox в UI нет. Audio response — private/no-store JSON с MIME, размером, SHA и base64 для Blob playback; URL не несёт credential. Загрузка до 8 МиБ декодируется host-side в проверенный mono PCM WAV 24kHz/3–60s, без сетевых decoder protocols. Профиль хранит digest reference; удаление стирает WAV и оставляет минимальный tombstone от позднего idempotent повтора. Пробы используют request-specific profile и не переключают активный голос.
# Forge team candidate contracts

Exact worker `payload`/`events` reads are read-only recovery actions; an input/read error returns to the model and never creates an uncertain mutation. Result pages use `payload_sha256`, with `result_sha256` accepted for `kind=result`; the hash remains mandatory and conflicting hashes are refused. Coordinator checkpoint JSON belongs in `plan`; the same object in `message` is accepted for compatibility. Active-run orientation uses manifest summaries and bounded live-manifest observations, without reducing historical tool ledgers; unavailable observations remain visible as unknown, and coding tasks are included even when no durable run is active.

Legacy custody can be reconciled explicitly through `forge_recovery.reconcile_unit` on the verified local execution host. A receipt binds original request/result/state hashes, the exact canonical launch event, task root, physical host identity and a witnessed subsequent kernel reboot. Device/foreign-host execution, post-boundary launches, current bound supervisors, changed subjects and unbound histories remain fenced. Original outcomes and Stop records are retained; extinction releases custody but does not invent success or a clean Stop. Recovery is an audited operator migration, never an age/PID-based background expiry.

Task/worker result JSON and append-only journals remain canonical. A finished execution is distinct from coordinator acceptance: dependencies bind exact accepted result.json SHA-256, and upstream revision changes invalidate downstream eligibility transitively. Rework and continuation retain old identities/results; continuation requires proven old-writer exit, a settled checkpoint and compatible authority/tools/provider route. Unknown side effects are reconciled, never blindly replayed.

Only one writer owns a physical shared root, across aliases/overlapping paths and different task IDs. Independent roots and read-only roles can progress concurrently. Parent→editing-child transfer is explicit and blocks the parent until that branch releases custody. Detached/local/remote/check jobs retain ownership through confirmed descendant exit. Status readers project receipts without admission/migration writes.

New task policy is explicit; legacy missing active cap migrates with exact preimage/receipt to null (uncapped), not a new fallback limit. Unit iterations survive resume. The tree usage ledger distinguishes observed tokens and unknown spend; null optional tree caps and unknown cost are not zero budgets.

Independent reviewers read a frozen subject using exclusive read tools. A/L/B/M merging has its own isolated typed adapter, followed by separate critics and review of corrections. Critic verdict, actual coverage, tested candidate identity and author decision remain separate. Ordinary findings never create a new merge veto; author override does not rename findings to clear.

Owner UI reads canonical task/unit identities and exact payload hashes. Control is intent→single runner claim→actual receipt; a claimed crash stays unknown. Bound review/merge tasks inherit coordinator Stop/Pause fences. Real review outcomes enter canonical Forge wake as their own schema, not fabricated worker completions.

The main context accumulation implementation is retained: continuation_mode=legacy preserves the complete previous first provider input and appends newly admitted material only. Audience/privacy/authority/history/compaction/epoch/system/tools/model/render fences remain explicit. Worker system input is frozen for its execution, all transcript/tool outputs and available native sidecars are durable; incompatible route changes refuse continuation. Source tests do not establish real provider cache hits.

Frozen review qualification: verification_plan exports immutable source identity to a separate task; the coordinator chooses an isolated executor. bind_verification requires the entire canonical plan, actual passing non-deduplicated checks and complete exact logs. A subsequent fresh review includes those logs only for the same composition. Passing commands do not prove container isolation. Linux commands use a per-command subreaper; detached descendants keep ownership until ECHILD settlement, and a lost helper leaves custody unknown.

Independent-review corrections: Finish uses the same unresolved-root/job predicate as admission. Direct selected-agent Stop includes all branch-owned command/check units and retains unknown exit. Foreground commands publish a starting intent first, create a suspended/gated private custodian, persist its exact identity, then explicitly admit the executable; a dead custodian before admission cannot start the command. Linux helper settlement evidence is retained in the unit directory.
Forge worker provider calls explicitly opt into llm.single_provider_attempt: SDK retries, adapter empty retries and automatic fallback are disabled only in that context. A transport response or uncertain admission is retained, never replayed; only a typed proof of non-admission can use the existing outer retry allowance. Main voice callers retain their prior policy. This is an explicit worker custody contract, not a new API cache field or main context policy.
Review/merger starts reserve once under the coordinator lock using a fresh record, and admission updates cannot overwrite another record revision. Battery status rechecks producer digests and archived report/log bytes; fresh critic subjects lose qualification when that evidence changes. Owner projections include agents, processes and verifications, with unknown exit and pending root release visible; records and log references remain private. Control parsing errors retain original bytes with an unknown receipt and do not suppress unrelated durable wake delivery.

### Scoped search and outline (R4 candidate; not a host sandbox)

`fs_search` uses one resolved root across literal, regex and composite-glob routes.
An explicit `root` is resolved like `_resolve_read`: relative to BASE, allowed if
its resolved target is within BASE **or REPO** (these coincide in production).
Without `root`, the search scope is BASE, not the union with REPO. Literal home
search partitions that scope for budgets; regex uses one traversal. Existing
priority, budgets, memory/runs default exclusion, timeout/partial reporting and
128 MiB per-file stat-before-read threshold remain; this is not an RSS bound.
Results are named relative to the selected root (home partitions restore prefixes).

A glob is a relative selector within that root: absolute masks, backslashes and
any `..` component are rejected, including a `..` that would end back in-root.
File symlinks are included only when their resolved target is in the selected
search root, not merely somewhere in BASE/REPO. Broken/cyclic/out-of-scope links
are skipped. Implicit directory symlinks are **never traversed**, even when they
point in-root: this excludes cycles and duplicate trees, including explicit glob
components such as `link/*.py`, and applies also to home partition discovery.
An explicitly selected root may itself be a symlink or contain symlink components:
its resolved target must pass BASE/REPO resolution, and becomes the search scope.
Thus selecting `root=link` deliberately differs from implicit traversal of `link`.
Direct `hands.search` resolves within its supplied home (no separate REPO authority)
and accepts composite globs rather than silently discarding directory components.
Its optional `scope_root` keeps a shared scope across budget partitions for every
mask route; it must resolve within that home and contain the traversal root.
Directory-name exclusions apply only below the selected traversal root, never to
its ancestors or the selected root itself, and do not exclude matching filenames.

`code_outline` resolves the individual file with the same BASE/REPO read semantics;
it has no narrower search-root selector. Valid in-home/in-REPO file links and path
components are supported; out-of-home targets are refused. Direct `hands.outline`
uses only its supplied home. Outline is line-based, not AST-based (text resembling
`def` in multiline strings may appear). All these scope statements describe stable
filesystem resolution, **not** a race-proof sandbox against concurrent namespace
replacement. No restrictions are added to independent sovereign host/root/shell
capabilities; `execute`'s existing cwd contract is unchanged. Search file content
is still read only in the isolated child; silence never triggers a parent fallback.

## Пакетная правка файлов — 10.11.2026

`edit(action="batch", files=[...])` — общий редактор модели и воркера.
`praxis_hands.edit(files=[...])` выводит batch из наличия files. Каждый элемент содержит
`path`, `expected_sha256` прочитанного файла и `edits=[{"old": "...", "new": "..."}]`.
Пакет не смешивается с одиночными аргументами. Один путь указывается один раз.

По умолчанию `mode="snapshot"`: все old ищутся независимо в первоначальном UTF-8
снимке. Каждая строка должна встречаться ровно один раз, включая перекрывающиеся
вхождения; пересечение диапазонов разных замен отклоняет пакет. Явный
`mode="sequential"` разрешает последующей замене видеть предыдущую. В обоих
режимах все замены одного файла вычисляются до его единственной публикации.
CRLF и байты вне замен сохраняются.

До первой записи проверяются все пути, версии и замены. Несовпадение означает
отказ всему пакету без записи. Затем файлы публикуются и перечитываются по очереди
под общими блокировками. Это не транзакция нескольких файлов: первая ошибка
останавливает дальнейшие записи, автоматического отката нет. Каждый файл имеет
`confirmed`, `refused` или `in_doubt`, исходный и наблюдённый итоговый SHA.
Общий `partial` означает смесь подтверждённых и отказанных; `in_doubt` имеет
приоритет. Следующие файлы явно помечаются как не начатые. Восстановление — отдельная
правка после чтения текущих версий. Внешний shell-писатель не участвует в замке;
обычный rename не устраняет гонку с ним между последней проверкой и публикацией.

Python-пример:

```python
import praxis_hands as h
text, version = h.read("workspace/example.py")
receipt = h.edit(files=[{
    "path": "workspace/example.py", "expected_sha256": version,
    "edits": [{"old": "before", "new": "after"},
              {"old": "second", "new": "replacement"}]
}])
```

Успешный пакет возвращает расписку с `files`. `Refused`, `Partial`, `InDoubt`
содержат ту же расписку в `.result`; исключение нельзя трактовать как откат.
Локальная одиночная replace/write возвращает новый SHA. В удалённых Forge-задачах
host/Windows пакет пока честно отказывает до вызова удалённого редактора:
серверный backend не реализует пакетную предпроверку. Одиночные правки остаются
доступны. `continuation_mode=legacy` и выключенный pointer mode не меняются.

Восстановление владения старого локального процесса принимает прежний формат
request без `root`, только если `cwd` точно равен корню его задачи. По-прежнему
обязательны явная аттестация хоста, точное событие запуска до наблюдаемой
перезагрузки и отсутствие живого связанного supervisor. Это отдельная операторская
процедура `forge_recovery.reconcile_unit`, не автоматическое снятие `lost`.
Исходные request/state/result сохраняются; новая расписка освобождает владение,
не объявляя историческую команду успешной.


## Единый редактор — 10.11.2026

Публичная рука `edit(action="replace|write|patch|batch", ...)` заменяет прежние
fs_edit/coding_edit без алиасов. Без task_id адресует дом, сохраняя зоны записи,
proposal_id для ядра, overwrite/force для полной перезаписи. С task_id использует
корень и исполнитель Forge; patch (git apply --check/apply) требует задачи.
Отдельного fs_write и алиасов больше нет. Ответ edit: JSON: status/message/before_sha256/after_sha256;
пакет дополнительно содержит files. Patch возвращает ok без обещания SHA-readback.
Модель и воркер используют общий редактор со своими описаниями; воркер получает task_id/parent от runtime.

Локальный foreground coding_run/run передаёт Python-скрипту контекст своей команды.
h.read/h.edit/h.write/h.search наследуют задачу; явный конфликтующий task_id запрещён.
Редактор сверяет nonce с хешем в durable request, PID с birth identity и реальное
происхождение вызывающего процесса от custodian через Linux /proc. Только после
этого собственный process unit не считается конкурентом; чужие задачи/процессы,
воркеры, проверки, родительская цепь и Stop/Pause сохраняют все прежние запреты.
Это кооперативный custody-протокол, не sandbox от намеренно злонамеренного shell
с тем же UID и доступом к state. Контекст не логируется и не передаётся в argv.
Внешний Python может назвать task_id явно, но не получает исключения из владения.
Наследование гарантировано только для Linux local foreground commands; background,
remote и Windows не получают обещания этого режима. Без связанной задачи h.search ищет дома с root. Ошибки редактора Refused/Partial/InDoubt несут полный .result.


Удалённые одиночные операции передают структурированный ответ serverd/body до
его превращения в текст. Успех исполнителя возвращает ok (без местного SHA/readback
обещания), неокончательные/потерянные ответы и неоднозначные ошибки — in_doubt.
Известное отсутствие исполнителя до отправки — refused. Детали и request/operation
identity сохраняются в backend_result. Удалённый batch по-прежнему не отправляется.


## Каталог и живой Python — 10.11.2026

После слияний A/Б полный канонический owner-каталог содержит 78 схем (101 исходная,
минус 8 удалённых и 16 объединённых, плюс python). Исполняемых алиасов удалённых
имён нет; исторические записи и backend helpers сохранены. `catalog_tools_for`
собирает полномочия текущего хода, `_bind_tool_catalog` сохраняет этот полный набор.
Явный override специального хода остаётся узким. Каталог одинаков для любого
говорящего (Егор 10.11: «ВЕЗДЕ ВСЕ ПРАВА»; admit/computer_access удалены — 76 схем).
Раскрытие приватного по-прежнему проверяется на выходе.

Егор 10.11: `h.hand` — инструмент скриптов, а не экономия. При выключенных старых
указателях `offered_tools_for` отдаёт все схемы каталога родными; Python-хвоста нет.
Дополнительные reply/end_turn/task_control и provider tools зависят от вида хода. K
содержит карту семейств, общий словарь исходов и договор `h.hand` для скриптов;
`h.describe(name)['content']` возвращает точную схему текущего каталога. Старый pointer
mode, continuation_mode=legacy и авторское описание computer не меняются.

`python(code,cwd,timeout,task_id)` передаёт код через stdin интерпретатору без bash;
строки traceback соответствуют коду, h подключён заранее. `command_execution`
объединяет запуск shell/python/coding_run, сохраняя Forge admission и custody.
Git-наблюдение начинается после admission и возвращает [command]/[changes]/[exit].
Changes — наблюдаемая разница byte/mode/HEAD рабочего Git, без утверждения об
авторстве одновременных изменений или наблюдении внешних эффектов. Домашние фоновые
потомки переживают нормальное завершение; timeout закрывает обычную группу.
Удалённый task python пока явно отказывается до исполнения.

`run_command(argv,cwd,timeout,task_id,stdin_text)` (кандидат 14.11) запускает одну
программу массивом без shell: элементы не склеиваются и не разбираются заново, glob,
переменные и кавычки остаются данными. Неверный argv, отсутствующие программа или cwd
— refused до запуска. Дом идёт тем же `_home_command` (права, cwd, safety point,
автокоммит, рельс хардбота по отображаемой строке), задача — тем же `forge.run`
(admission, custody, журнал, Stop; argv записан в request.json). stdin_text — точный
UTF-8 и EOF через безымянный поток; без него stdin закрыт. stdout/stderr объединены.
Строка `[run_command {...}]` называет executor, cwd, argv, найденную программу, exit
или signal, timeout и подтверждение остановки дерева; после timeout `[exit unobserved]`,
неподтверждённая остановка — in_doubt. Host serverd (`/bin/sh -c`) и Windows-задачи
(PowerShell) не имеют typed argv: run_command отказывает до отправки, без перехода в
строку. Воркер получает `run_command(argv,cwd,timeout,stdin_text)` с неявной задачей.
shell, worker `run` и `coding_run` остаются явными строковыми shell-операциями.
Рука `git` принимает `paths` массивом (кандидат 14.11): diff/add получают каждый
элемент одним pathspec (diff после `--`). Записанные до этого вызовы со строкой
воспроизводятся прежним разбиением по пробелам и помечаются в ответе.
`coding_process`/worker `process` start принимает либо `command` (shell-строка), либо
`argv` (одна точная программа); оба сразу, неверный argv, отсутствующие программа/cwd и
argv для host/Windows-задачи отказываются до старта. Тот же супервизор `forge_process.py`
(один владелец, start/poll/stop/list, ID и журнал): `request.json` хранит argv и
найденную программу, супервизор запускает массив без shell.
`run_command` также принимает `env` (массив пар name/value — strict-схемы OpenAI не
несут свободный словарь; PRAXIS_* отказывается, явный PYTHONPATH дописывается после
служебного), `outputs` (отчёт до/после: created/modified/unchanged/deleted/missing, sha256)
и `scratch` (только внутри cwd и не под Git; удаляется после запуска и не входит в [changes],
остаётся, если ничего не запускалось или остановка дерева не доказана). Строка фактов и
request.json называют только имена env; сам вход вызова, как любой аргумент, в журнале хода.

`hand_bridge.py` даёт foreground-команде приватный Unix socket (каталог 0700,
socket 0600), token только в окружении ребёнка и проверку SO_PEERCRED/UID/происхождения.
Вызов возвращается в настоящий родительский RunContext и ChannelContext; дочерний
Python не импортирует agent. `_AgentHandRuntime` использует тот же durable executor:
права и Stop, tool_started, стабильный ключ доставки, tool_report, result_ref и
paused/in_doubt. Вложенный hand_requested связывает вызов с внешним call_id;
critical-параметры и их digest проходят существующую redacted-проекцию.

```python
r = h.hand('proposal', action='list')
print(r['content'])
print(h.describe('proposal')['content'])
```

Ответ содержит status/outcome/content/result_ref/call_id/image_count. completed означает
полученную расписку; outcome — исход самой руки из её tool_result (confirmed/refused/
in_doubt/partial/ok/failed, unknown, если рука его не сообщает). Картинки попадают в
родительский кадр и immutable run artifacts; resume проверяет путь, SHA и размер.
end_turn, silence и ожидание доставки используют настоящее состояние родительского хода.
Когда скрипт перестал ждать (его timeout или выход), `Bridge.drain` отказывает новым
запросам и дожидается уже начатых, пока ран running без pause/cancel; их расписки
дописываются после [exit N] строкой `[late hand …]`. Ceiling внешней руки, pause или
cancel закрывают канал: pending становится in_doubt. Background survivors и отдельные
workers не получают живого диспетчера.
При повторе потерянного ответа внутри той же команды `_request_id` должен быть
тем же с точными аргументами: один effect, повтор cached receipt; иной intent
отказывается. Автоматических сетевых повторов и слепого replay эффектов нет.
Это кооперативный протокол того же UID, а не sandbox от sovereign shell/root.

Файловые руки h.hand наследуют nonce/custodian проверенной локальной задачи через
реальный PID Unix peer; чужой task_id отказывается. `fs_read(paths=[...])` даёт
пофайловые версии/ошибки/картинки; fs_read/fs_ls/fs_search принимают task_id.
Native coding_inspect больше не принимает read/list/search; приватный worker
inspect сохраняется. Чтения, списки, поиск и coding_inspect пишут исход в tool_report:
`forge.inspect` возвращает `InspectReply` (str с outcome), отказы помечены там, где
возвращаются, ответ удалённого backend остаётся unknown; пакет fs_read даёт один
итог ok/partial/refused/failed. `all=true` заменяет все непересекающиеся вхождения, confirmed
называет точное число. Его after_sha256 подходит для следующего expected_sha256.

Serverd договаривается о edit_all_v1 до all-запроса. Неизвестный/старый backend и
Windows явно отказываются, а не игнорируют параметр. Broker replace строго читает
UTF-8, сохраняет CRLF и сверяет текущие байты до записи. stage=preflight означает
доказанный отказ до публикации; apply/after_error или неокончательный ответ не
превращаются в refused. Remote ok остаётся подтверждением backend, без местной
readback-гарантии. Remote batch по-прежнему не отправляется.
