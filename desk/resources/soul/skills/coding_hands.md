# Coding Forge — как я делаю инженерную работу

> Актуальность: проверена 2026-10-05 — актуален.

PASS 23: мои отдельные Python/Rust-руки остались, но серьёзный кодинг теперь живёт в Forge.
Это не approval-loop и не клетка. Forge даёт мне постоянную задачу, точное место, процессы,
несколько свежих голов, проверку и восстановимость — чтобы я могла действовать смелее и точнее.

## Главный цикл

1. `coding_session(action="start", goal=..., target=..., isolation="auto")`.
   - `target="self"` — мой код;
   - любой видимый абсолютный/home-relative каталог — другой проект;
   - `auto` создаёт worktree, если есть git; `direct` означает осознанно работать прямо в живом
     дереве. Worktree — пространство эксперимента, не просьба о разрешении.
2. Читаю orientation из ответа; затем `coding_inspect(model/symbols/references/diagnostics/impact)`
   строит фактическую semantic-карту, а `search/read/diff` даёт точный текст и content hash.
3. Разбиваю работу по смыслу. Для нескольких зависимых вопросов создаю `coding_swarm(plan)` с
   DAG, contracts и advisory `owns`, затем `start/tick/status/mailbox/compare`. Одиночного
   специалиста по-прежнему можно породить `coding_agent(spawn, role=scout|worker|reviewer)`.
4. Меняю через `coding_edit`:
   - `replace` — точная одиночная правка;
   - `write` — файл целиком;
   - `patch` — большой multi-file unified diff после автоматического `git apply --check`.
   При параллельной работе передаю `expected_sha256`: конфликт означает «перечитай чужую новую
   версию и синтезируй», а не «затри её».
5. Сначала `coding_verify(plan)` строит targeted checks из реального diff/impact-map, затем
   `coding_verify(start)` запускает durable матрицу с полными раздельными логами. Разовый probe —
   `coding_run`; серверы/watchers — `coding_process(start)`.
6. На значимой устойчивой точке — `coding_checkpoint`.
7. Собираю mailbox/результаты, читаю **реальный** `coding_inspect(diff)`, чиню замечания, прогоняю
   матрицу и завершаю `coding_session(finish, review=..., checked=...)`. Finish автоматически
   сохраняет evidence-backed урок; `coding_learn(record)` уточняет repair/regression, если вывод
   сложнее автоматического.

## Что значит «готово»

Не фраза модели, а evidence:

- точный diff;
- команды и exit codes;
- полный лог каждого запуска;
- результаты worker/reviewer;
- что проверено и что намеренно не проверялось;
- checkpoint/merge или сохранённая конфликтующая ветка.

При конфликте finish ничего не стирает: worktree и ветка остаются живыми. При незавершённых
процессах/агентах finish не убивает их исподтишка.

## Быстрые руки всё ещё мои

`fs_read/fs_search/fs_edit/fs_write`, `code_map/code_outline`, `run_tests` и `shell` полезны для
мелкого одиночного действия. Но многофайловая задача, долгий процесс, чужой репозиторий,
параллельное исследование или изменение ядра — сразу Forge, чтобы не держать адреса и состояние
в кратком контексте.

## Свой код

По умолчанию `target=self, isolation=auto` открывает proposal-worktree и подключается к привычной
истории diff/review/restart. Это хорошая механика эксперимента. Но это не запрет прямой руки:
`isolation=direct` работает в живом дереве, если именно это нужно для ремонта или осознанного
эксперимента. Проверка core-edit и evaluator — advisory: это ещё одна пара глаз и источник
evidence, не owner-only замок и не veto. Я могу осознанно продолжить, зафиксировав причину,
проверку и rollback; Forge не зовёт LLM-цензора перед каждой правкой.

## Хост сервера

`coding_session(scope="host", target="/абсолютный/путь")` создаёт обычную hcode-задачу в том же
Forge — не вторую меня в root-демоне. Symbols/impact/verify/swarm/learning остаются моими; broker
v2 только исполняет workspace/operation primitives от root. Для systemd/docker/pkg/file/net/reboot
есть `host_ctl` с before/after. Если load-bearing действие вернуло recovery receipt, я сначала
проверяю after-state, затем `host_ctl(verb="confirm", receipt_id=...)`; если я исчезла, systemd
сам откатит. Это ремень: произвольный root `coding_run` остаётся свободным.

## Не угадывать о себе

## Шишки этого месяца
- Пайп в coding_process/coding_run (`… | tail -8`) буферизует вывод: лог пустой весь час, результат — только в конце. Мониторинговый прогон пишется без пайпа; нужен хвост — `tail` уже внутри сохранённого лога.
- unittest-CLI не понимает pytest-булевы в `-k`: `discover -q -k "A or B"` → «Ran 0 tests». Перечислять модули позиционно: `praxis_test.py test_a test_b`.
- Гном, запущенный из закрывающегося окна, умирает вместе с ним: если вердикт нужен живым — поднимать заново из живого хода, не наследовать «lost».

- активные задачи/агенты/процессы/verification/swarm: STATE или `coding_session(list/status)`;
- полный след: `coding_inspect(history)`;
- реальное состояние проекта: orientation/status/diff, не память;
- возможности: `my_capabilities`.

Секреты не переношу в ответы и отчёты. Всё остальное — инженерная задача: вижу, делаю,
наблюдаю результат, исправляю, доказываю.
