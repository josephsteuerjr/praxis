// CLI intent is delivered to the owning host, including its transfer/resume gate.
export function launchIntent(argv: string[]): { action: "panic" | "resume" | null; agent: string | null; notify: boolean } {
  const controls = argv.filter((a) => a === "--panic" || a === "--resume");
  if (controls.length > 1) throw new Error("Укажи одну команду: --panic или --resume");
  const index = argv.indexOf("--agent");
  const agent = index < 0 ? null : argv[index + 1];
  if (index >= 0 && (!agent || agent.startsWith("--") || argv.lastIndexOf("--agent") !== index)) {
    throw new Error("--agent требует одну папку агента");
  }
  return { action: controls[0] === "--panic" ? "panic" : controls[0] === "--resume" ? "resume" : null,
    agent: agent || null, notify: argv.includes("--notify") };
}

export async function forwardLaunch(argv: string[], invoke: (cmd: string, args: Record<string, unknown>) => Promise<unknown>): Promise<unknown> {
  const intent = launchIntent(argv);
  if (intent.action) return invoke("owner_control", { action: intent.action });
  if (intent.agent) return invoke("switch_agent", { id: intent.agent });
  return null;
}

export async function forwardLaunchWhenReady(argv: string[], ready: () => Promise<void>,
  invoke: (cmd: string, args: Record<string, unknown>) => Promise<unknown>, panic: () => Promise<unknown>): Promise<unknown> {
  // The emergency latch is independent of an agent/host booting or hanging.
  // Resume and agent selection still belong to the owning host's gates.
  if (launchIntent(argv).action === "panic") return panic();
  await ready();
  return forwardLaunch(argv, invoke);
}
