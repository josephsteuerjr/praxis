// Канал окна к агенту: адрес харнесса и ключ — ровно то, что прежняя оболочка
// (shell/src/main.rs: channel_script) клала в window.DESK_CONFIG_OVERRIDE.
//
// Пока «сердце» программы (helene-host) не переехало из Tauri, Electron читает то же сам:
// helene.json установки → дерево агента → memory/.state/desk-token. Детей он не поднимает:
// агента держит служба или прежнее окно; окно Electron только подключается к каналу.
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";

export const CONFIG_NAME = "helene.json";
const DESK_PORT = 8094; // common/agents.rs: DESK_PORT
const BASE_AGENT_ID = "main"; // common/agents.rs: BASE_AGENT_ID

export interface Channel {
  base: string;
  key: string;
  agent: string;
  agent_id: string;
  agents: Array<{ id: string; name: string; port: number; enabled: boolean; conflict: string; base: boolean }>;
  product: string;
  needs_remote?: boolean;
}

export type ConfigRead =
  | { kind: "ok"; value: Record<string, unknown> }
  | { kind: "missing" }
  | { kind: "broken"; why: string };

/** helene.json может прийти с BOM (Блокнот) — как decode_config в оболочке. */
export function readConfig(path: string): ConfigRead {
  if (!existsSync(path)) return { kind: "missing" };
  try {
    const text = readFileSync(path, "utf8").replace(/^﻿/, "");
    const value = JSON.parse(text);
    if (!value || typeof value !== "object" || Array.isArray(value)) return { kind: "broken", why: "в файле не объект" };
    return { kind: "ok", value };
  } catch (e) {
    return { kind: "broken", why: e instanceof Error ? e.message : String(e) };
  }
}

export function mtimeNs(path: string): string | null {
  try {
    return String(statSync(path, { bigint: true }).mtimeNs);
  } catch {
    return null;
  }
}

function str(v: unknown): string {
  return typeof v === "string" ? v.trim() : "";
}

/** Имя агента — как agent_name(): agent.name, иначе telegram.agent_name, иначе «Агент». */
export function agentName(cfg: Record<string, unknown> | null): string {
  const agent = (cfg?.agent ?? {}) as Record<string, unknown>;
  const tg = (cfg?.telegram ?? {}) as Record<string, unknown>;
  return str(agent.name) || str(tg.agent_name) || "Агент";
}

export function resolveIn(base: string, raw: string): string {
  return isAbsolute(raw) ? raw : resolve(base, raw);
}

/** Дерево агента: `tree` из helene.json относительно папки файла, по умолчанию data. */
export function treeOf(configPath: string, cfg: Record<string, unknown> | null): string {
  return resolveIn(dirname(configPath), str(cfg?.tree) || "data");
}

function deskToken(tree: string): string {
  try {
    const t = readFileSync(join(tree, "memory", ".state", "desk-token"), "utf8").trim();
    return t.length >= 16 && t.length <= 128 && /^[A-Za-z0-9]+$/.test(t) ? t : "";
  } catch {
    return "";
  }
}

export function channelFor(root: string, product: string): { channel: Channel; note: string } {
  const path = join(root, CONFIG_NAME);
  const read = readConfig(path);
  const cfg = read.kind === "ok" ? read.value : null;
  const agent = agentName(cfg);
  const empty: Channel = { base: "", key: "", agent, agent_id: BASE_AGENT_ID, agents: [], product };
  if (read.kind === "broken") return { channel: empty, note: `${path} не разобрался: ${read.why}` };
  if (!cfg) return { channel: empty, note: `${path} нет — программа не настроена` };
  const mode = str(cfg.mode);
  if (mode === "remote") {
    const base = str(cfg.base);
    return {
      channel: { ...empty, base, key: str(cfg.key), needs_remote: !base },
      note: base ? `удалённый агент: ${base}` : "адрес сервера не вписан",
    };
  }
  const port = typeof cfg.port === "number" ? cfg.port : DESK_PORT;
  const tree = treeOf(path, cfg);
  const key = deskToken(tree);
  const me = { id: BASE_AGENT_ID, name: agent, port, enabled: true, conflict: "", base: true };
  return {
    channel: { ...empty, base: `http://127.0.0.1:${port}`, key, agents: [me] },
    note: key ? `канал 127.0.0.1:${port}, ключ из ${tree}` : `ключ канала не прочитан из ${tree} — окно получит 403`,
  };
}
