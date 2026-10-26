# Python tools and Forge in Hélène 1.7.0

Hélène includes Python tools for files, code, projects and execution. Forge adds
durable coding tasks, workers, checkpoints and verification records around those
operations. Tool availability depends on the channel, configuration and granted
permissions; the System view shows the current catalogue.

## Changes in 1.7.0

- Nested worker tasks are linked to their parent tasks and results in the workshop
  view. Completion of a worker and acceptance of its result are separate records.
- Focus reads use addressable pages and source fingerprints. Truncated output is
  marked explicitly.
- Tool details show submitted requests and results. Direct Python file operations
  are distinguished from shell execution.
- Process records distinguish a stop request from confirmed termination. Uncertain
  descendant state remains visible and can be checked again.
- The System view distinguishes directly offered tools from catalogue-based calls.
- The Windows updater verifies the embedded Python import policy before runtime
  reuse, including access to the installed Forge tree.

The tool families below already exist; this release improves their visibility
and the Forge workflow around them.

## Tool families

| Operations | Purpose |
|---|---|
| `fs_read`, `fs_ls`, `fs_search` | Read files, list directories and search a project |
| `fs_edit`, `fs_write` | Apply exact replacements and create files with write checks |
| `code_outline`, `code_map` | Inspect Python definitions and project structure |
| `project_create`, `project_list`, `project_status` | Create projects and inspect their state |
| `run`, `run_tests`, `pip_install` | Execute commands, run checks and install project dependencies |
| `send_file` | Deliver a file through the available channel |
| Forge operations | Manage tasks, workers, checkpoints, checks and saved results |

These are Python implementation operation names. A model-facing schema may group
them or expose them through a catalogue. Direct file operations do not require
a shell. The UI records a submitted command request; it does not present that
request as an independently observed OS argument vector.

## Forge task lifecycle

A task records its goal, source directory, constraints and saved state. Workers
can create child tasks. Parent tasks retain links to their workers and results,
so a completed child turn can be reviewed before its result is accepted.
Worktrees, checkpoints and journals support continuation across turns.

### Shared-root teams in 2.2

Read-oriented scouts and reviewers may run concurrently. Editing workers share one writer slot per task root. If an editing worker delegates another editor, `delegate(action="wait", agent_id=...)` yields that slot to one immediate worker child and blocks the parent until its result is settled. The parent must first settle detached process/check jobs; parent Stop/Pause still fences the child. After the exact result returns, record its disposition before treating it as accepted work. A queued child is not already executing, and a completed result does not itself release dependent DAG nodes.

Rework creates a new linked attempt, retaining the previous output and acceptance history. Invalidating an upstream acceptance also invalidates its transitive dependents' current eligibility without erasing their historical receipts. Worker journals retain full tool payloads and resumable checkpoints; an unknown in-flight call is not automatically replayed.

For large files, focus reads return pages with a source fingerprint. Partial
output remains marked as partial. A request to stop a process is recorded
separately from confirmation that its descendants have stopped. POSIX process
groups are not a sandbox against a process deliberately calling `setsid`.

## Custom Python tools

An extension supplies a manifest and a `register(api)` entry point. The loader
checks API compatibility, tool schemas, declared capabilities and name conflicts.
An optional acceptance function can check compatibility before an upgrade.

For example, an extension directory under `data/extensions/example/` can contain
the following `extension.json`:

```json
{
  "name": "example",
  "version": "1.0",
  "api": "helene.ext/1.0",
  "requires": {"helene": ">=1.7.0"},
  "entry": "example:register",
  "capabilities": [],
  "acceptance": "example:check"
}
```

Its `example.py` registers a tool:

```python
TOOL = {
    "description": "Return the extension's status.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}


def status() -> str:
    return "ready"


def register(api):
    api.register_tool(
        "example_status", TOOL, status,
        purpose="Inspect extension status", capabilities=(),
    )


def check(api):
    return True
```

Registration closes when `register()` returns. Tools that need capabilities such
as `fs.read`, `fs.write`, `http` or `shell` must declare them in the manifest and
at registration. Extensions remain subject to channel permissions, path policies
and self-development rules.

The existing extension contract is documented in
[`desk/resources/РАСШИРЕНИЯ.md`](../resources/РАСШИРЕНИЯ.md).
The updater can check extensions against the incoming runtime before replacing
the installation and records the compatibility result.

## Runtime and upgrades

Python, Git and voice dependencies are bundled. On Windows, embedded Python uses
the relative `..\tree` entry in `python314._pth` to import the installed Forge
tree. The updater checks that file's SHA-256 against the build passport before
reusing a runtime. An incompatible older import policy is not carried forward.
Authored constitutions, skills, memory and code use the existing preservation
and merge mechanisms.

The four additional default skills cover secret handling, owner-authorized
infrastructure changes, Windows paths with spaces, and verification with rollback
reporting. Upgrades add the new files and refresh an unmodified default index;
authored changes are preserved.

## Verification and platform requirements

The release publishes build passports, individual SHA-256 files, `SHA256SUMS`,
a release receipt and a verification archive. Checks on unchanged source,
final package bytes, disposable installations and native CI are identified
separately. They do not establish acceptance on an owner's physical machine.

- Linux computer control uses X11/AT-SPI; Wayland has explicit limitations.
- Rocky/RHEL 8 needs EPEL for `xdotool` and `xclip`.
- macOS requires Apple Silicon, macOS 14+ and the corresponding TCC permissions.
- File tree operations reject symlink ancestors in destination paths. On macOS,
  use a canonical temporary path such as `/private/var/...`.

[Downloads and verification receipts](https://github.com/josephsteuerjr/praxis/releases/tag/v1.7.0).
