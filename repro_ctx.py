# -*- coding: utf-8 -*-
"""Доезжает ли её слово `task_control` до цикла, если тул исполняется КАК В ЖИЗНИ.

ИСТОРИЯ. Первая редакция `work_loop` держала слово в `contextvars`, а руки исполняются в
`contextvars.copy_context()` в отдельном потоке (`_call_tool_with_ceiling`). Запись уходила
в копию и умирала вместе с потоком — `taken()` у вызывающего возвращал None ВСЕГДА.

⚠ ПОПРАВКА К САМОМУ ЭТОМУ ПРИБОРУ, 11.08. Первая его редакция звала руку БЕЗ привязанного
прогона. Пока состояние жило в contextvars, это было неважно. После починки состояние лежит
в словаре по `run_id` — и прибор без прогона стал показывать «не доезжает» на исправном
коде, то есть врать в другую сторону. В жизни прогон есть всегда: `task_window` привязывает
его вокруг всего хода (`run_context.bind_run(durable)`), и рука исполняется внутри. Прибор
обязан воспроизводить ЭТОТ путь целиком, иначе он меряет не то.
"""
import os
import sys

os.environ["PRAXIS_WORK_LOOP"] = "on"
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "test")
os.environ.setdefault("PRAXIS_TEST", "1")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import agent          # noqa: E402
import run_context    # noqa: E402
import work_loop      # noqa: E402

print("TOOL_CEILING_SEC =", getattr(agent, "TOOL_CEILING_SEC", "нет"),
      "(ноль означал бы, что рука идёт в том же потоке и проба вакуумна)")

run = run_context.RunContext.create(
    kind="task_window", goal="проба рабочего хода", principal_id="probe", scope="owner")

with run_context.bind_run(run):
    work_loop.reset()

    print("\n--- 1. НАПРЯМУЮ, как звали мои первые тесты ---")
    out = agent.tool_task_control("done", evidence="прямой вызов")
    print("    вернул:", out[:80])
    print("    taken() у вызывающего:", work_loop.taken())

    work_loop.reset()
    print("\n--- 2. ЧЕРЕЗ ПОТОЛОК РУКИ, как в жизни ---")
    impl = agent.TOOL_IMPL["task_control"]
    out = agent._call_tool_with_ceiling(
        "task_control", impl, {"action": "wait", "wake_on": "жду реле"})
    print("    вернул:", str(out)[:80])
    taken = work_loop.taken()
    print("    taken() у вызывающего:", taken)
    print("    decide():", work_loop.decide(kind="task_window", control=taken)[0],
          "(False = ход закрыт её словом, как и должно быть)")

    print("\n--- 3. слово одного прогона не видно из другого ---")
    other = run_context.RunContext.create(
        kind="task_window", goal="соседний прогон", principal_id="probe", scope="owner")
    with run_context.bind_run(other):
        print("    taken() в соседнем прогоне:", work_loop.taken())
    work_loop.release(other.run_id)

    broken = taken is None
    work_loop.release(run.run_id)

print("\nВЕРДИКТ:", "СЛОВО НЕ ДОЕЗЖАЕТ — основание сломано" if broken
      else "слово доезжает: рука в другом потоке, а ход закрывается её словом")
sys.exit(2 if broken else 0)
