"""Typed search subprocess transport through the canonical owned launcher."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile


def call(argv, request, *, timeout=None):
    import command_execution
    import run_context
    import step_control
    context = run_context.current_run()
    if context is None:raise ValueError('owned search requires an explicit run context')
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output:
        source.write(json.dumps(request, ensure_ascii=False).encode('utf-8'));source.seek(0)
        process = command_execution.launch(argv, cwd=Path.cwd(),
            stdin=source, stdout=output, defer_start=True, parent_lifeline=True)
        try:
            if step_control.requested(context.run_id):
                raise step_control.StepCancelled('search cancelled before effect admission')
            process.admit()
            code = process.wait(timeout)
            if step_control.requested(context.run_id):
                raise step_control.StepCancelled('owned search cancelled')
            if code != 0:return None
            output.seek(0)
            value = json.load(output)
            return value if isinstance(value, dict) else None
        except BaseException:
            process.stop_tree()
            raise
