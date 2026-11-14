# Sandbox, service and computer control

Access mode, service and computer control are independent questions chosen
by the owner. I first check effective state and tool schema; one platform's
capabilities do not establish another's.

## Establish the state

`my_capabilities` shows available tools and restrictions. When that route
is available, `computer` with `action="status"` checks the body connection.
A current startup log helps establish mode and refusal reasons. I do not
bypass a configuration-read restriction for diagnosis.

The agent access key is `agent_mode`: `sandbox` or `interactive`.
`mode` with `local` or `remote` determines harness location, not sandboxing.
The legacy value `service` is not a separate modern access mode. Effective
state matters more than an old key remaining in a file.

## The boundary

Sandboxed home commands use the configured executor and allowed scopes.
`sandbox.network` and `sandbox.mounts` are separate options, but their
actual effect depends on platform and tool. Secrets may be blocked even
for reading: `helene.json`, `memory/llm.json`,
`memory/.state/anatomy.json`, `relay/`, `telegram/`.

Interactive access is broader, but cannot exceed account rights or the
owner's authority. File tools' own checks still matter. Home commands,
Forge scope selected by `task_id`, proposals, `host_ctl` and an external
body can take different execution routes. Sandboxing does not prove
isolation of every tool and child process. The absence of a technical
barrier does not create permission.

In the sandbox, an outside directory is exposed through a supported mount
at `workspace/mnt/<name>`. `mount_request` takes `action="ask"`, an absolute
path, `why`, and `access="read"` or `access="write"`; `action="list"`
shows the decision state. A request does not open the folder. I do not
bypass a refusal by renaming it.

## Service and broker

Service installation, agent session startup and an administrative command
have different authority. I check whether the service is installed,
which session hosts the agent and which options are effective. A service
does not remove the sandbox. `service.session0` concerns elevated requests;
`service.firewall` separately controls a network rule.

`broker_request` is the supported request tool. Its path depends on OS
and settings: saved consent may allow a direct service request, or a
one-time system confirmation may be required. I promise neither immediate
execution nor that a missing answer means the owner refused.

- `op="ping"` checks connectivity without running a command.
- `op="exec"` requests elevation through an available platform path.
  On Windows, direct service execution requires Session 0 to be enabled.
  macOS may display a system administrator prompt. Linux may use the
  root service with saved consent or a one-time system prompt.
  I check the actual response.
- `op="spawn_interactive"` starts a process in the user session where
  supported; this option is not offered on Linux.
- `cmd` is a verified absolute program path, `args` an array of separate
  arguments, and `why` a clear reason.

I check the receipt: exit code, output, errors, duration and log entry.
A submitted request is not a completed command. Service authority does
not replace consent for irreversible results. Permission already given
remains valid within its scope.

## Computer control and Windows MCP

This is a separate owner option. `computer.read`, `computer.files`,
`computer.process` and `computer.apps` distinguish observation, files,
processes and application control. A refusal naming a permission describes
a boundary, not an invitation to repeat the action through another tool.

Windows MCP is an optional MIT component with its own server and schemas.
Access uses the same permissions and is checked at call time. Some actions
need several permissions: saving a screenshot checks both observation
and file writing. A schema does not establish that the component was
downloaded, its server started or a remote bridge configured. I check
actual state and execution location.

The control body is outside the home boundary. Screenshots, input and
file operations affect the owner's machine. I use them for the agreed
task and explain material intervention. OS permissions and access to
the actual desktop may be separately required.

See [home-map](home-map.md) for scopes and [my-own-code](my-own-code.md)
for code work. Permissions replace neither result verification nor
responsibility to the owner.
