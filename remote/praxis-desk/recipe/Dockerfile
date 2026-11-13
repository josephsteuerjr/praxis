# Пульт: канал desk рядом с деревом агента, в своём контейнере.
#
# Зачем этот файл появился 10.09. Контейнер `praxis-desk` жил ОБРАЗОМ БЕЗ ИМЕНИ,
# собранным месяцем раньше для другого дела (в нём лежал весь стек её бота —
# aiogram, anthropic, av, cairo — только ради того, что там уже стоял aiohttp).
# Пересобрать Пульт было нечем, а `docker image prune` унёс бы его молча.
# Теперь рецепт объявлен здесь и едет на сервер вместе с пакетом desk.
#
# Сам desk в образ НЕ копируется: он монтируется каталогом
# (`/opt/praxisdesk` -> `/desk:ro`), и выкладка пакета меняет его без пересборки
# образа. В образе — только Python и зависимости канала, а их список пакет
# объявляет сам: `requirements-desk.txt` пишет `deskpkg.build`.
FROM python:3.12-slim

# Только канал: ни git, ни ffmpeg, ни STT здесь не нужны — deskapp не зовёт
# ни одного внешнего процесса (проверено грепом `subprocess` 10.09).
COPY requirements-desk.txt /tmp/requirements-desk.txt
RUN pip install --no-cache-dir -r /tmp/requirements-desk.txt

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 HELENE_HOST=0.0.0.0
EXPOSE 8094
CMD ["python", "/desk/deskapp.py"]
