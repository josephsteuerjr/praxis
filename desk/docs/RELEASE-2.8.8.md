# Hélène 2.8.8 Beta — all platforms, Latest

This release includes the native server-bridge installation repair and the work-PC
acceptance follow-up. The SSH proxy and optional permanent HTTPS bridge are built in;
no separate Tray Bridge runtime is needed.

- The native server-bridge install button enables its local module without stopping
  the running session or calling the relay-only binary installer.
- Proxy setup is accessible directly and starts with an existing server. An existing
  tinyproxy can be connected using a saved SSH profile or private key, a verified host
  fingerprint and independently chosen local/remote ports, without changing that server.
  Proxy-only operation needs no domain, Caddy or reverse HTTPS forwarding.
- After the real SSH/proxy check, the selected agent can use the route without copying
  loopback addresses between cards. Relay proxying is an explicit separate choice;
  settings show when saving and restarting are still required. Busy ports, changed
  host keys, rejected credentials and unreachable SSH have distinct diagnostics.
  Existing external tunnels are never stopped or adopted implicitly.
- An unreadable optional Forge liveness record no longer takes down the whole roster.
  Its participant is shown with unknown process liveness and a visible diagnostic;
  the damaged bytes and canonical results remain intact. Explicit Forge/reviewer
  disclosure choices survive reloading the page.
- A verified Mini App menu button remains ready when the optional bot username lookup
  fails. Public HTTPS, bot reception, menu publication and real WebView authorization
  remain separate checks.
- Historical in-doubt actions are marked as requiring reconciliation instead of being
  shown as completed turns. Media delivery is never confirmed or repeated merely by
  upgrading; existing evidence-based reconciliation remains available to the agent.
- Windows edge/corner resizing returns to the coalesced fixed-edge resize API for
  compatibility with the reported Windows 10 failure. Responsive native movement by
  the header remains. Maximized windows hide all resize handles; the close control
  reaches the extreme top-right screen pixel.

## Verification scope and limits

Fresh targeted regressions include real isolated SSH forwarding/reconnect/port and
host-key refusal, production browser proxy setup and relay opt-in, corrupted Forge
observations, disclosure recreation, Mini App menu readback, and all eight physical
mouse resize directions on a disposable Windows 11 WebView2 stand. The actual compiled
desktop UI is checked for maximized edge/close hit targets. Native package results,
full frontend/Node gates, package/updater and public byte checks are recorded in the
release evidence produced for the final frozen source.

The core, bundled Python runtime and relay are unchanged from 2.8.7. Their earlier
full qualification is bound to exact unchanged inputs rather than described as a
fresh full core run. Historical inherited core failures remain recorded.
Physical acceptance on the reporting Windows 10 computer, a real user Telegram
conversation and Telegram WebView entry are separate from isolated fixtures.
An old media delivery with an unknown outcome requires actual evidence; this release
does not fabricate its delivery or erase its journal. No owner installation or
production Praxis instance is changed by the release job.

macOS archives are ad hoc signed, not notarized. The ARM64 server retains relay 1.2.0;
desktop packages retain the existing subscription relay and its upgrade behavior.
