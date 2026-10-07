import { existsSync } from "node:fs";
import { dirname, isAbsolute, join } from "node:path";
import { execFileSync } from "node:child_process";

export function programRoot(executable: string, override?: string): string {
  if (override) { if (!isAbsolute(override)) throw new Error("HELENE_PROGRAM_ROOT должен быть абсолютным путём"); return override; }
  let dir = dirname(executable);
  for (let i = 0; i < 6; i++) {
    if (existsSync(join(dir, "helene-build.json"))) return dir;
    dir = dirname(dir);
  }
  return "/opt/helene";
}
export function ownerRoot(home: string, override?: string): string {
  if (override && !isAbsolute(override)) throw new Error("HELENE_ROOT должен быть абсолютным путём");
  return override || join(home, ".local", "share", "helene");
}
export function initOwner(program: string, root: string, override = false): void {
  if (override) {
    if (!existsSync(join(root, "helene.json"))) throw new Error("В HELENE_ROOT нет helene.json");
    return;
  }
  const report = JSON.parse(execFileSync(join(program, "helene-svc"), ["home"], { encoding: "utf8", timeout: 30_000 }));
  if (!report.ok || report.home !== root || report.conflicts?.length || report.missing_in_program?.length) {
    throw new Error("Не удалось подготовить дом Hélène: " + JSON.stringify(report));
  }
}

/** The Rust bootstrap is authoritative, including its foreign-port verdict. */
export function bootstrapChannel(script: string): Record<string, unknown> {
  const match = /^window\.DESK_CONFIG_OVERRIDE = (\{[^\n]*\});/.exec(script.trim());
  if (!match) throw new Error("Движок не передал канал окна");
  return JSON.parse(match[1]);
}
