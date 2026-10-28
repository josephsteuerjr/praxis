# active_workspace:praxis/frame_literal.py
"""Литеральный рендер дословного кадра durable-слоя; читатель, а не писатель.

Что это: offline-читатель одного прогона ``memory/runs/<run_id>``, который
находит событие ``model_input`` (последнее или по ``call_id``), читает тройку
manifest.json + events.jsonl + results/NNNN-model-input.log, ВАЛИДИРУЕТ её
через ``keat_source.adapt_model_input`` и рендерит человекочитаемый Markdown:
system/messages — дословно, схемы рук — количеством и именами.

Чем НЕ является: это не возврат теневого писателя кадров и не подача в модель
(#111932 — дословные кадры уже пишутся самим durable-слоем). Никакого захвата,
сборки, кандидирования или провайдерных вызовов; на диск не пишет без ``--out``.

Границы честности: рендер точен до байтов ВАЛИДИРОВАННОГО durable-JSON, но сам
durable-слой пишется через структурный скрабблер (agent.py
``_scrub_critical_value``/``_durable_model_messages``), поэтому эти файлы НЕ
сертифицируют точный вход провайдера — см. docs/keat-source-adapter.md.
Положительный признак в журнале — ``metadata.receipt.scrubbed_possible`` у
события model_input (прибор frame_trace); его отсутствие НЕ означает отсутствия
скраба. Пины читатель вычисляет сам из прочитанных байтов: независимый якорь —
sha256 из ResultRef события, поэтому подмена файла ловится сверкой с журналом.
Это инструмент локальной проверки на доверенной машине, а не пин-провайдер для
чужих данных.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from keat_candidate import ContractError
from keat_source import RunProvenance, SourcePins, adapt_model_input

_NAMESPACE = "praxis-durable-runs"  # имя хранилища, а не локальный путь
_PROV_FIELDS = tuple(RunProvenance.__dataclass_fields__)
_REF_PATH = re.compile(r"results/\d{4,12}-model-input\.log$")


class FrameLiteralError(RuntimeError):
    """Durable-тройка не прошла валидацию; частичный рендер не отдаётся."""


def _read_bytes(run_dir: Path, name: str) -> bytes:
    try:
        return (run_dir / name).read_bytes()
    except OSError as exc:
        raise FrameLiteralError(f"не читается {name} в {run_dir}: {exc}") from exc


def _provenance(manifest_bytes: bytes) -> RunProvenance:
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        context = manifest["context"]
        return RunProvenance(**{k: context[k] for k in _PROV_FIELDS})
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise FrameLiteralError(f"в manifest.json нет полей RunProvenance: {exc}") from exc


def _select_model_input(events_bytes: bytes, call_id: str | None) -> dict:
    rows = []
    for line in events_bytes.splitlines():
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise FrameLiteralError(f"строка events.jsonl не является JSON: {exc}") from exc
        if isinstance(row, dict) and row.get("kind") == "model_input":
            rows.append(row)
    if not rows:
        raise FrameLiteralError("в журнале нет событий model_input")
    if call_id is None:
        return rows[-1]
    for row in rows:
        if row.get("call_id") == call_id:
            return row
    known = ", ".join(sorted({str(r.get("call_id")) for r in rows}))
    raise FrameLiteralError(f"model_input с call_id={call_id!r} не найден; есть: {known}")


def _verbatim(body: str, label: str | None = None) -> str:
    # Длина fence выше самого длинного ряда бэктиков в теле — чтобы дословный
    # текст не закрыл себе границу.
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    fence = "`" * max(3, longest + 1)
    head = f"{fence} [{label}]" if label else fence
    return f"{head}\n{body}\n{fence}"


def _content(content) -> str:
    if isinstance(content, str):
        return _verbatim(content)
    if isinstance(content, list):
        parts = []
        for block in content:
            if (isinstance(block, dict) and block.get("type") == "text"
                    and isinstance(block.get("text"), str)):
                parts.append(_verbatim(block["text"], "text"))
            else:
                parts.append(_verbatim(json.dumps(block, ensure_ascii=False, indent=2),
                                       str(block.get("type")) if isinstance(block, dict)
                                       else type(block).__name__))
        return "\n".join(parts)
    return _verbatim(json.dumps(content, ensure_ascii=False, indent=2))


def _tool_line(tool) -> str:
    if not isinstance(tool, dict):
        return f"- {type(tool).__name__} (не объект схемы)"
    name = str(tool.get("name") or tool.get("type") or "?")
    schema = tool.get("input_schema")
    if isinstance(schema, dict):
        props = schema.get("properties")
        params = (f"; параметры: {', '.join(map(str, props))}" if props else
                  "; без свойств")
        return f"- {name} — {schema.get('type', '?')}{params}"
    return f"- {name} — встроенная рука провайдера (без input_schema)"


def _scrub_note(row: dict) -> str:
    receipt = row.get("metadata") if isinstance(row.get("metadata"), dict) else None
    flag = receipt.get("scrubbed_possible") if isinstance(receipt, dict) else None
    marker = ("да" if flag is True else "нет") if isinstance(flag, bool) \
        else "признак в событии отсутствует"
    return ("durable-JSON точен до байтов, но пишется через структурный скрабблер — "
            "точный вход провайдера он не сертифицирует (docs/keat-source-adapter.md); "
            f"metadata.receipt.scrubbed_possible события: {marker}")


def render_run(run_dir: Path, call_id: str | None = None) -> str:
    """Валидировать и отрендерить один model_input-кадр прогона."""
    run_dir = Path(run_dir)
    manifest_bytes = _read_bytes(run_dir, "manifest.json")
    events_bytes = _read_bytes(run_dir, "events.jsonl")
    provenance = _provenance(manifest_bytes)
    row = _select_model_input(events_bytes, call_id)
    ref = row.get("result") if isinstance(row.get("result"), dict) else {}
    path = str(ref.get("path") or "")
    if not _REF_PATH.fullmatch(path):
        raise FrameLiteralError(f"ResultRef ведёт не на model-input-файл: {path!r}")
    model_input_bytes = _read_bytes(run_dir, path)
    try:
        pins = SourcePins(_NAMESPACE,
                          hashlib.sha256(manifest_bytes).hexdigest(),
                          hashlib.sha256(events_bytes).hexdigest(),
                          hashlib.sha256(model_input_bytes).hexdigest())
        snapshot = adapt_model_input(
            manifest_bytes=manifest_bytes, events_bytes=events_bytes,
            model_input_bytes=model_input_bytes, pins=pins,
            expected_provenance=provenance,
            call_id=str(row.get("call_id") or ""))
    except ContractError as exc:  # включает UnsupportedSource
        raise FrameLiteralError(
            f"keat_source не принял durable-тройку "
            f"(call_id={row.get('call_id')!r}, result={ref.get('result_id')!r}): {exc}") from exc
    model, provenance = snapshot.model_input, snapshot.provenance
    lines = [
        "# Литеральный кадр durable-слоя (model_input)",
        "",
        f"- run_id: `{provenance.run_id}`",
        f"- call_id: `{row.get('call_id')}`",
        f"- result_id: `{ref.get('result_id')}` ({ref.get('path')}, "
        f"{len(model_input_bytes)} байт)",
        f"- provenance: principal `{provenance.principal_id}`, scope "
        f"`{provenance.scope}`, origin {provenance.origin_chat_id!r}, "
        f"delivery {provenance.delivery_chat_id!r}",
        f"- model_input sha256: `{pins.model_input_sha256}`",
        f"- сообщений: {len(model.get('messages') or [])}; "
        f"схем инструментов: {len(model.get('tools') or [])}",
        f"- scrub boundary: {_scrub_note(row)}",
        "",
        "## system",
        "",
        _content(model.get("system")),
        "",
        "## messages",
        "",
    ]
    for index, message in enumerate(model.get("messages") or []):
        body = message.get("content") if isinstance(message, dict) else message
        role = message.get("role") if isinstance(message, dict) else type(message).__name__
        lines += [f"### [{index}] {role}", "", _content(body), ""]
    tools = model.get("tools") or []
    lines += ["## tools", "",
              f"Всего схем: {len(tools)} (полный JSON каждого уже в durable-файле).", ""]
    lines += [_tool_line(tool) for tool in tools]
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Дословный Markdown-рендер model_input-кадра durable-прогона "
                    "(с валидацией keat_source).")
    parser.add_argument("run_dir", type=Path,
                        help="каталог прогона, например memory/runs/2026-10/<run_id>")
    parser.add_argument("call_id", nargs="?", default=None,
                        help="call_id нужного model_input (умолчание: последний)")
    parser.add_argument("--out", type=Path, default=None,
                        help="необязательный путь файла; без него — печать в stdout")
    args = parser.parse_args(argv)
    try:
        text = render_run(args.run_dir, args.call_id)
    except FrameLiteralError as exc:
        print(f"frame_literal: {exc}", file=sys.stderr)
        return 2
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
