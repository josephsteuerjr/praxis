// Private pipe to the shared Rust runtime. No second supervisor lives in Node.
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { EventEmitter } from "node:events";
import { createInterface } from "node:readline";

export class HostClient extends EventEmitter {
  private child: ChildProcessWithoutNullStreams;
  private seq = 0;
  private pending = new Map<number, { resolve(v: unknown): void; reject(e: Error): void }>();
  private dead = false;
  readonly ready: Promise<{ script: string; version: string }>;
  constructor(executable: string, root: string, log: (line: string) => void) {
    super();
    this.child = spawn(executable, ["--root", root], { cwd: root, stdio: ["pipe", "pipe", "pipe"], windowsHide: true });
    this.child.stderr.on("data", (data) => log(`host: ${String(data).trim()}`));
    let accept!: (value: { script: string; version: string }) => void;
    let reject!: (error: Error) => void;
    this.ready = new Promise((ok, fail) => { accept = ok; reject = fail; });
    const timeout = setTimeout(() => reject(new Error("Движок оболочки не ответил при запуске. См. helene.log.")), 60_000);
    const fail = (error: Error) => {
      if (this.dead) return;
      this.dead = true;
      clearTimeout(timeout);
      reject(error);
      for (const p of this.pending.values()) p.reject(error);
      this.pending.clear();
      this.emit("closed", error);
    };
    this.child.on("error", (e) => fail(e));
    this.child.on("exit", (code, signal) => fail(new Error(`Движок оболочки завершился (${signal ?? code}).`)));
    createInterface({ input: this.child.stdout }).on("line", (line) => {
      let message: { id?: number; result?: unknown; error?: string; event?: string; data?: any };
      try { message = JSON.parse(line); } catch { log("host: unexpected output (see host log)"); return; }
      if (message.event) {
        if (message.event === "ready") { clearTimeout(timeout); accept(message.data); }
        this.emit("event", message.event, message.data);
        return;
      }
      if (!Number.isSafeInteger(message.id)) return;
      const request = this.pending.get(message.id!);
      if (!request) return;
      this.pending.delete(message.id!);
      if (message.error != null) request.reject(new Error(message.error));
      else request.resolve(message.result);
    });
  }
  invoke(command: string, args: Record<string, unknown> = {}): Promise<unknown> {
    if (this.dead) return Promise.reject(new Error("Движок оболочки не работает. Перезапусти окно."));
    const id = ++this.seq;
    const line = JSON.stringify({ id, command, args }) + "\n";
    const maxBytes = command === "local_files_save" ? Math.ceil(64 * 1024 * 1024 / 3) * 4 + 65536 : 16 * 1024 * 1024;
    if (Buffer.byteLength(line) > maxBytes) return Promise.reject(new Error("Слишком большой запрос к оболочке."));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.child.stdin.write(line, (error) => {
        if (error) { this.pending.delete(id); reject(error); }
      });
    });
  }
  async close(): Promise<void> {
    if (this.dead) return;
    // EOF also invokes the Rust cleanup; do not detach or orphan the host.
    let timeout: ReturnType<typeof setTimeout> | undefined;
    await Promise.race([
      this.invoke("host_shutdown").catch(() => undefined),
      new Promise<void>((resolve) => { timeout = setTimeout(() => { this.child.kill("SIGTERM"); resolve(); }, 5000); }),
    ]);
    clearTimeout(timeout);
    this.child.stdin.end();
    await new Promise<void>((resolve) => {
      if (this.dead) resolve();
      else this.child.once("exit", () => resolve());
    });
  }
}
