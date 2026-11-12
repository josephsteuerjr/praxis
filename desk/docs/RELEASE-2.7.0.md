# Hélène 2.7.0 Beta

Your agent can move to a server and return with its current memory, code,
projects and Git history. Review what travels before the move; archives are
checked before activation, interrupted downloads resume, and previous copies
remain available. Returning stops the server copy and leaves local resumption
to you. A server-born agent is not yet covered by the return workflow.

- A server bridge is available from the first-run guide and regular settings.
  Save an SSH connection, confirm its host fingerprint, inspect the installation
  plan, or connect to an existing setup. The bridge keeps proxy and permanent
  HTTPS connections alive; Caddy handles certificates for the configured domain.
  Existing sites and services are preserved. The ⓘ guide explains setup and recovery.
- Telegram's Mini App now presents the full programme, including agent selection
  and settings. Desktop-only actions explain where they must be performed.
  Owner Telegram sign-in is required; existing QR device grants do not gain these rights.
- Telegram polling, messages, checks and menu updates share one network policy.
  Bot connection, public HTTPS, menu publication and Mini App authorization have
  separate status and retry actions. Choose automatic Cloudflared or your own HTTPS.
- The main chat and the owner's Telegram conversation share context. Work started
  in focus mode retains its return destination for the result.
- Agents can enable their own reflection and schedule several alarms in one call.
  Workflow skills cover planning, finishing work and following through on promises.
- Model catalogues persist and can be refreshed. Model selection is kept consistent;
  the old owner-pinned model restriction is removed.
- Tray activation brings the window forward. Updates preserve startup intent;
  settings offer the required engine, window or programme restart with accurate wording.
- The window bounds cached turn details and uses a lighter activity animation,
  addressing growth in memory and continuous rendering work during long sessions.
- Computer.run now forwards timeout_ms to Body's process deadline. It rounds up
  to whole seconds; omitted or zero means no deadline. Async run/poll/stop remain
  available. The separate read_window action accepts max_text_chars.

Download the archive for your platform, extract the complete folder and run Hélène.
This is a Beta release published as Latest for the built-in updater. Qualification
receipts, platform limits and device acceptance are recorded in the release evidence;
source tests alone do not prove acceptance on a particular work PC or phone.
