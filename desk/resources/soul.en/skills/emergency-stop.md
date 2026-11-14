# Emergency stop in Hélène

For dangerous work, available `panic(reason)` latches a durable installation
stop and initiates termination of agent work. Closing a window is not
equivalent: a background session may remain alive.

The owner can use Emergency Stop in the window, tray or phone application,
an installed emergency shortcut, or `/panic` to their bot. The form
`/panic@BOT_USERNAME` needs that bot's actual username. The Telegram path
checks the configured owner before a model turn.

On Windows, establish the actual program path first.
CMD: `"<absolute-program-path>\helene.exe" --panic`.
PowerShell: `& "<absolute-program-path>\helene.exe" --panic`.
If the service utility is bundled, `helene-svc.exe panic` is available
from its directory. Do not assume a Program Files installation.

Opening a window, restarting or updating is not consent to clear Stop.
Resume requires an explicit owner action or supported command
`helene-svc.exe resume`. The agent must not bypass that decision with
a deferred process.

The flag prevents new startup. Individual process outcomes need separate
checks and may remain unknown. A flag does not prove the whole tree
exited or every already-issued external action was cancelled.
