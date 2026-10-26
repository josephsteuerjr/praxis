# Durable-run утечки: принятый task_control не стал terminal

Когда живой список показывает paused/blocked ран, а в events.jsonl лежит принятый
`task_control(done|wait|blocked)` — это не «забудь и закрой руками». Это два
известных слоя, и каждый чинится своим швом:

## Слой 1: слово гасится пост-цикловой доставкой
Post-loop outbound guard читает run-wide receipt (`outbound-guard:<run_id>`) и
сверяет draft sha. Новый draft после возобновления ≠ старый receipt →
DurableExecutionError → generic `running→paused process_recovery` поверх её слова.
Шов: `_land_addressee_free_work_control` в `_AgentResumeRuntime` (садит слово ДО
`_prepare_authored_delivery` для addressee-free ранов). Смёржено 13c954fb.

## Слой 2: план не строится вообще
Тот же исторический guard-ordering валидируется ещё в `plan_resume` preflight —
ран, отравлённый до слоя 1, не доходит до исполнения и стучит `resume_attempt_idle`
сутками. Шов: plankind `checkpoint_control` — для addressee-free work-ранов без
outstanding calls и без checkpoint outbound садит точное integrity-checked слово
из чекпойнта до guard-валидации. Lease/cursor compare сохранён (forged grant
роняет исполнение), addressed/chat путь не тронут. Смёржено 226ab141.

## Порядок диагностики
1. `events.jsonl`: искать `tool_result` c `task_control.log` и последний
   `run_checkpoint` → прочитать его `work_loop.control`.
2. Решить, какой слой: если план вообще `blocked` на outbound guard — слой 2;
   если план исполнялся и упал в `continue_checkpoint` — слой 1.
3. Никогда не терминализировать manifest вручную без этих улик: слово должно
   прийти из durable evidence, иначе это подделка моего же решения.

## Проверка
- `python praxis_test.py test_work_wait_survives.py test_reviewer_probes.py`
- Живьём: `agent.resume_durable_run(rid)` → ожидать `not_resumable` «run is
  terminal (done)» и `status_changed done` + `recap_written` в events.

## Наблюдение
Старые разнородные paused/blocked раны (wait-парковки, owner-pause, orphans без
checkpoint) — НЕ этот класс; их закрывать оптом нельзя.
