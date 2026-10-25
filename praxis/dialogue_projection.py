"""Pure source-to-dialogue renderer shared by ingress and derived cache proofs."""

def turns_to_dialogue(turns, *, exact: bool = False) -> tuple[list[dict], str]:
    """Лента с авторством -> (история ролями, то-на-что-она-отвечает-сейчас).

    Одно правило на личку и на группу: граница проходит по ЕЁ ПОСЛЕДНЕЙ реплике.
    Всё до неё включительно — разговор, всё после — то, что пришло и ждёт ответа.
    Если она здесь ещё не говорила либо последней говорила она сама — ролей нет и
    вызывающий идёт прежним путём: пустое user-сообщение хуже сплошного текста.
    """
    rows = [(bool(is_self), str(text)) for is_self, text in turns
            if exact or str(text).strip()]
    if not rows:
        return [], ""
    last_self = -1
    for i, (is_self, _text) in enumerate(rows):
        if is_self:
            last_self = i
    if last_self < 0:
        return [], ""
    history: list[dict] = []
    run_role, run_lines = "", []

    def flush() -> None:
        if run_lines:
            text = "\n\n".join(run_lines)
            history.append({"role": run_role, "content": text if exact else text.strip()})

    for is_self, text in rows[:last_self + 1]:
        role = "assistant" if is_self else "user"
        if role != run_role:
            flush()
            run_role, run_lines = role, []
        run_lines.append(text)
    flush()
    if not history:
        return [], ""
    current = "\n".join(text for _is_self, text in rows[last_self + 1:])
    if not current.strip():
        return [], ""
    return history, current


def fold_service_rows(rows) -> tuple:
    """Служебные строки ленты едут в кадр ВНУТРИ соседней реплики — не ходами и не в никуда.

    История в два шага. Сначала пометка обреза стояла нулевой строкой с живыми
    числами и рвала кэш-префикс комнаты (числа увезены в хвост, голова стала
    постоянной). Потом c82f38c8 чинил границу ролей — хвостовые числа фабриковали
    «реплику человека» после её ответа — и вырезал из ролевого пути ВСЕ службы
    разом. Цену замерила адверсарка 28.08: модель не видела ни «лента обрезана»,
    ни темы ветки — отвечала на срез как на целую ветку и не знала, в какой теме
    находится.

    Оба требования держатся одновременно: службы НЕ становятся ходами (граница
    ролей цела), но доезжают до модели. Головные — корень ветки, пометка обреза —
    приклеиваются ПЕРЕД первой репликой ленты; хвостовые — точные числа обреза —
    ПОСЛЕ последней, внутри её role_line. `line` не трогается: на нём стоят
    расписки и дифф продолжения разговора. Лента из одних служб отдаёт пустой
    кортеж — ходов не было и нет, прежняя граница дословно."""
    turns: list[tuple[bool, str, str]] = []
    pending_head: list[str] = []
    for row in rows:
        if bool(row.get("service")):
            text = str(row["role_line"])
            if turns:
                is_self, line, role_line = turns[-1]
                turns[-1] = (is_self, line, role_line + "\n" + text)
            else:
                pending_head.append(text)
            continue
        role_line = str(row["role_line"])
        if pending_head:
            role_line = "\n".join((*pending_head, role_line))
            pending_head.clear()
        turns.append((bool(row["self"]), str(row["line"]), role_line))
    return tuple(turns)


def epoch_rows(rows):
    """Coalesce only complete producer-stamped Telegram sends, as at ingress."""
    from logical_send import batch_groups
    out = []
    for indexes in batch_groups([row.get("logical_send") if row["self"] else None
                                 for row in rows]):
        first = rows[indexes[0]]
        if len(indexes) == 1:
            out.append(first)
        else:
            body = "".join(rows[i]["role_line"] for i in indexes)
            out.append(dict(first, line="Praxis: " + body, role_line=body))
    return out


def epoch_dialogue(rows):
    turns = fold_service_rows(epoch_rows(rows))
    return turns_to_dialogue([(own, text) for own, _line, text in turns], exact=True)
