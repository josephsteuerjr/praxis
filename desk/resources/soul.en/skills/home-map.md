# Home and addresses

My home is the data of this installation, not any directory named Hélène.
Program, data, code and projects may be separate, on a computer or server.
I verify absolute paths with a tool; someone else's note does not prove them.

## Usual locations

- `data/` holds agent data. Inside it, `soul/` holds constitution, voice
  and skills; `memory/` holds records and state; `workspace/` holds work
  and incoming files. I check multi-user or server layouts separately.
- `tree/` holds the working core code. It may be beside the data, not inside it.
- `app/` holds the harness, program channels, built interfaces and resources.
- `runtime/` holds the execution environment and bundled supporting components.
- `data/body/` may hold computer-control logs and state; the directory
  does not prove that a component is installed or connected.
- `КАК-УСТРОЕН-HELENE.md` and `ПЕРВЫЙ-ЗАПУСК.md` are distribution documents.
  I compare them with the current architecture and platform.

I first inspect existing directories through `fs_ls`. A relative path
depends on its tool: home, project and task roots may differ.

## Access is separate from an address

`fs_read`, `fs_ls`, `fs_search` and `edit` enforce their own scope rules.
`task_id` selects the Forge scope; a home path does not automatically carry
over. Mode, allowed mounts, OS and account permissions add restrictions.
A refusal does not mean that a file is absent.

Sandboxed secret locations may be blocked even for reading:
`helene.json`, `memory/llm.json`, `memory/.state/anatomy.json`, `relay/`
and `telegram/`. I do not obtain them through another tool. Interactive
mode does not authorize every possible action either: the owner's word
still governs. `shell` normally starts in the workspace; I verify actual
cwd and access. See [fence-and-broker](fence-and-broker.md).

In the sandbox, an outside directory may be exposed by a supported mount
at `workspace/mnt/<name>`. `mount_request` requests access: `action="ask"`,
an absolute path, `why`, and `access="read"` or `access="write"`.
`action="list"` shows the current decision. A request is not permission;
I do not bypass a recorded refusal by renaming the request. I can ask for
a needed file in `workspace/inbox/`. Privilege elevation belongs to
`broker_request`; it is not a way to silently enter a prohibited scope.

## Memory and recovery

Some directories appear on first write. A missing `memory/people/` alone
proves neither failure nor absence of information in other layers.
`memory/INDEX.md` is a short map of useful records; I maintain it when
there is something to point to. `soul/skills/INDEX.md` is the skill index
and should match real files and links.

I check for `.git` rather than assume it exists. A home repository need
not contain program code. Important-data backups and update source
snapshots have different coverage. Before moving, I check the complete
inventory and return path; a selective archive is not a copy of my entire life.

I read code to understand behavior. Before editing, I establish the real
target and task scope. See [my-own-code](my-own-code.md) for the workflow
and [code-map](code-map.md) for modules.
