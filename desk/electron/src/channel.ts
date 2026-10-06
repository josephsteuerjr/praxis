// Канал окна к агенту: адрес харнесса и ключ — ровно то, что прежняя оболочка
// (shell/src/main.rs: channel_script) клала в window.DESK_CONFIG_OVERRIDE.
//
// Пока «сердце» программы (helene-host) не переехало из Tauri, Electron читает то же сам:
// helene.json установки → дерево агента → memory/.state/desk-token. Детей он не поднимает:
// агента держит служба или прежнее окно; окно Electron только подключается к каналу.
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
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

/** Имя агента: agent.name → telegram.agent_name → fallback (у соседа — его id,
 *  у корневого — «Агент»), как agent_title в common/agents.rs. */
function titleOf(cfg: Record<string, unknown> | null, fallback: string): string {
  const got = str((cfg?.agent as Record<string, unknown>)?.name) || str((cfg?.telegram as Record<string, unknown>)?.agent_name);
  return got || fallback;
}

/** id папки годится, как и в common/agents.rs: латиница-цифры-дефис, ≤32. */
function idOk(name: string): boolean {
  return /^[a-z0-9][a-z0-9-]{0,31}$/.test(name);
}

/** Один агент установки: как AgentEntry в common/agents.rs — порт, дерево, включён. */
export interface RosterAgent {
  id: string;
  name: string;
  port: number;
  enabled: boolean;
  conflict: string;
  base: boolean;
  tree: string;
}

const ROSTER_DIR = "agents"; // common/agents.rs: ROSTER_DIR

/**
 * Все агенты установки — то же правило, что `roster()` в common/agents.rs:
 * корневой первым (порт из его helene.json или 8094), соседи по алфавиту из
 * agents/<id>/helene.json (порт из конфига или 8094+index). Спор за порт не
 * прячет агента из списка: он показывается с пометкой conflict.
 */
export function rosterFor(root: string): RosterAgent[] {
  const baseConfig = join(root, CONFIG_NAME);
  const baseRead = readConfig(baseConfig);
  const baseCfg = baseRead.kind === "ok" ? baseRead.value : null;
  const basePort =
    typeof baseCfg?.port === "number" && baseCfg.port >= 1 && baseCfg.port <= 65535
      ? baseCfg.port
      : DESK_PORT;
  const out: RosterAgent[] = [
    {
      id: BASE_AGENT_ID,
      name: titleOf(baseCfg, "Агент"),
      port: basePort,
      enabled: baseCfg?.enabled !== false,
      conflict: "",
      base: true,
      tree: treeOf(baseConfig, baseCfg), // конфига нет/бит — всё равно data рядом с ним
    },
  ];
  const taken: Array<{ port: number; id: string }> = [{ port: basePort, id: BASE_AGENT_ID }];
  let kids: string[] = [];
  try {
    kids = readdirSync(join(root, ROSTER_DIR), { withFileTypes: true })
      .filter((e) => e.isDirectory())
      .map((e) => e.name)
      .sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
  } catch {
    // папки соседей нет — в установке один корневой, это не ошибка
  }
  let index = 0;
  for (const name of kids) {
    if (!idOk(name) || name === BASE_AGENT_ID) continue;
    const config = join(root, ROSTER_DIR, name, CONFIG_NAME);
    if (!existsSync(config)) continue;
    index += 1;
    const read = readConfig(config);
    const cfg = read.kind === "ok" ? read.value : null;
    const port =
      typeof cfg?.port === "number" && cfg.port >= 1 && cfg.port <= 65535
        ? cfg.port
        : DESK_PORT + index;
    let conflict = "";
    const holder = taken.find((t) => t.port === port);
    if (holder) conflict = holder.id;
    else taken.push({ port, id: name });
    out.push({
      id: name,
      name: titleOf(cfg, name),
      port,
      enabled: cfg?.enabled !== false,
      conflict,
      base: false,
      tree: treeOf(config, cfg), // битый конфиг — агент всё равно при своём data
    });
  }
  return out;
}

/**
 * Фикс-волна 06.10 (F4): канал окна собирает НЕ этот файл. Единственный
 * живой путь — init-script от Rust-оболочки (channel_script в main.rs):
 * она же поднимает и держит агентов, и ключ/порт окна обязаны приходить от
 * неё, а не от второго читателя helene.json, который разошёлся бы с первым.
 * `rosterFor` остаётся живым (roster для канала зовёт bootstrapChannel);
 * ЭТА функция — не экспорт поведения, а справочник протокола для стендов
 * Electron-лейна. Задел под «второй читатель» (agent_id всегда main внутри)
 * был миной: любой будущий вызов подсвечивал бы не того агента.
 */
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
  const roster = rosterFor(root);
  const me = roster.find((a) => a.id === BASE_AGENT_ID)!;
  return {
    channel: {
      ...empty,
      base: `http://127.0.0.1:${port}`,
      key,
      agents: roster.map(({ id, name, port: p, enabled, conflict, base }) => ({
        id, name, port: p, enabled, conflict, base,
      })),
      agent_id: me.id,
    },
    note: key
      ? `канал 127.0.0.1:${port}, агентов ${roster.length}, ключ из ${tree}`
      : `ключ канала не прочитан из ${tree} — окно получит 403`,
  };
}
