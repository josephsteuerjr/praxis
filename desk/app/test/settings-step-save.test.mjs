// «Сохранить без правок» не предлагает перезапуск из-за собственной записи
// ступени (аудит 1.3.6–1.3.9, остаточный дефект после 74cc11b).
//
// Сценарий живой жалобы владельца: открыть Настройки → выбрать верхнюю ступень
// («Разрешить права СИСТЕМЫ» — ключ service.session0 уходит в файл НЕМЕДЛЕННО,
// мимо черновика, src/session0-persist.ts) → нажать «Сохранить», ничего больше
// не меняя. До фикса расписка вечно твердила «применится перезапуском»:
//
//   1. РАМКА сравнивала черновик со срезом МОМЕНТА ОТКРЫТИЯ экрана, а файл уже
//      изменила сама карточка. У «Перезаписать своим» база была свежая
//      (config_load перед записью) — асимметрия двух кнопок одного экрана.
//      Фикс: freshness.accept(base===виденный mtime) перечитывает файл и
//      тянет срез c вперёд — своя запись перестаёт быть «чужой правкой».
//   2. КАРТОЧКА сравнивала выбранную СТУПЕНЬ лестницы (picked, бывает
//      "session0") с ОГРАДОЙ из трубы (live.name — всегда забор) — разница
//      вечна, и note()/syncPlan() держали хвост «перезапусти» и план
//      «ограда сменится» при согласованных ступени и заборе.
//
// Исполняется НАСТОЯЩИЙ код каркаса — вырезки из исходника собираются во
// временный .ts-модуль и импортируются (как session0-persist.ts в соседнем
// стенде): уедет строка — стенд покраснеет, а не замолчит. Пересказов нет.
//
// Запуск: node app/test/settings-step-save.test.mjs (из корня desk/ или app/).

import { readFileSync, writeFileSync, rmSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { tmpdir } from "node:os";
import { strict as assert } from "node:assert";

import { persistSession0 } from "../src/session0-persist.ts";

const here = dirname(fileURLToPath(import.meta.url));
const frameSource = readFileSync(join(here, "..", "..", "ui-kit", "window", "views", "settings-frame.ts"), "utf8");
const modeSource = readFileSync(join(here, "..", "src", "modecard.ts"), "utf8");

const tick = () => new Promise((r) => setTimeout(r, 0));

// --- вырезки из каркаса (настоящий код, не пересказ) -----------------------
const blocksFrom = frameSource.indexOf("export const RESTART_BLOCKS");
const blocksTill = frameSource.indexOf("export interface Config");
const acceptFrom = frameSource.indexOf("freshness.accept = (base, fresh) =>");
const acceptTill = frameSource.indexOf("class StaleConfig", acceptFrom);
assert.ok(blocksFrom > 0 && blocksTill > blocksFrom, "blocksNeedingRestart не нашёлся в settings-frame.ts — стенд отстал от файла");
assert.ok(acceptFrom > 0 && acceptTill > acceptFrom, "freshness.accept не нашёлся в settings-frame.ts — стенд отстал от файла");

// Временный .ts-модуль: функции каркаса как есть (с их типами — их снимет
// Node), accept — обёрнут в фабрику с подставными shell/seenMtime/c.
const standDir = join(tmpdir(), "helene-step-save-stand");
mkdirSync(standDir, { recursive: true });
const standFile = join(standDir, "stand.ts");
writeFileSync(standFile, frameSource.slice(blocksFrom, blocksTill) + `

export const makeAcceptStand = (shell, seenMtime, c) => {
  const freshness = { accept: null };
${frameSource.slice(acceptFrom, acceptTill)}
  return { freshness, get seen() { return seenMtime; }, get c() { return c; } };
};
`);
const { blocksNeedingRestart, makeAcceptStand } = await import(pathToFileURL(standFile).href);

/** Подставной мир рамки: срез открытия, файл, который вернёт config_load. */
function acceptStand({ seenMtime = "100", snapshot = {}, fileAfter = null, fileMtime = null } = {}) {
  const calls = [];
  const shell = async (name) => {
    calls.push(name);
    if (name === "config_load") return { config: fileAfter, mtime_ns: fileMtime };
    throw new Error(`неожидали команду ${name}`);
  };
  // Спред вычислил бы геттеры сразу — снапшот вместо живого значения;
  // поэтому calls дописываем в сам объект стенда.
  const stand = makeAcceptStand(shell, seenMtime, snapshot);
  stand.calls = calls;
  return stand;
}

// --------------------------------------------------------------------------
// 1. Сценарий целиком: ступень записана немедленно → Сохранить без правок →
//    blocksNeedingRestart ПУСТ, потому что срез c догнал собственную запись.
// --------------------------------------------------------------------------
{
  // Файл на открытии экрана: ограда-песочница, ключа ступени в файле нет.
  const fileAtOpen = {
    agent_mode: "sandbox",
    sandbox: { enabled: true, network: true },
    service: { firewall: true },
  };
  // Подставная оболочка: один файл, отпечаток меняется с каждой записью.
  let disk = JSON.parse(JSON.stringify(fileAtOpen));
  let mtime = 100;
  const shell = async (name, args) => {
    if (name === "config_load") return { config: disk, mtime_ns: String(mtime) };
    if (name === "config_save") {
      disk = JSON.parse(args.config);
      mtime += 100;
      return { ok: true, mtime_ns: String(mtime) };
    }
    throw new Error(`неожидали команду ${name}`);
  };

  // Шаг 1: карточка пишет ступень немедленно (настоящий persistSession0).
  const stamp = await persistSession0(shell, true);
  assert.deepEqual(stamp, { base: "100", fresh: "200" }, "запись ступени вернула пару отпечатков");

  // Шаг 2: рамка принимает свою запись — срез c тянется к живому файлу.
  const stand = acceptStand({
    seenMtime: "100",
    snapshot: fileAtOpen,
    fileAfter: disk,
    fileMtime: stamp.fresh,
  });
  stand.freshness.accept(stamp.base, stamp.fresh);
  await tick(); await tick();
  assert.equal(stand.seen, "200", "отпечаток рамки шагнул к своей записи");
  assert.deepEqual(stand.c, disk, "срез c перечитан: собственная запись не «чужая правка»");

  // Шаг 3: «Сохранить» без правок. Черновик — срез открытия; collect карточки
  // дописывает в него то же, что карточка уже положила в файл (ступень).
  const out = JSON.parse(JSON.stringify(fileAtOpen));
  out.service = { ...out.service, session0: true };
  assert.deepEqual(blocksNeedingRestart(stand.c, out), [],
    "ступень записана немедленно, правок нет — а расписка предлагает перезапуск");
  // Контроль неотрицательности стенда: blocksNeedingRestart — настоящая и
  // смену блока всё же видит, значит пустой ответ выше — не пустышка сравнения.
  const fenceChange = JSON.parse(JSON.stringify(out));
  fenceChange.sandbox.enabled = false;
  assert.deepEqual(blocksNeedingRestart(stand.c, fenceChange), ["ограда"],
    "стенд ослеп: перестал видеть настоящую смену блока перезапуска");
}

// --------------------------------------------------------------------------
// 2. Границы принятия: чужую правку accept не легализует ни в каком виде.
// --------------------------------------------------------------------------
{
  // База не совпала (файл менял кто-то между открытием и записью карточки):
  // ни отпечаток, ни срез не двигаются — конфликт покажет «Сохранить».
  const foreign = acceptStand({ seenMtime: "100", snapshot: { sandbox: { enabled: true } } });
  foreign.freshness.accept("999", "200");
  await tick();
  assert.equal(foreign.seen, "100", "непроверенная база сдвинула отпечаток рамки");
  assert.deepEqual(foreign.c, { sandbox: { enabled: true } }, "непроверенная база заменила срез конфига");
  assert.deepEqual(foreign.calls, [], "без совпавшей базы рамка не ходит в файл вовсе");
}
{
  // Гонка: файл перечитался, но отпечаток уже не тот, что вернула запись, —
  // между записью карточки и перечитыванием файл трогал кто-то ещё. Срез
  // остаётся прежним: принять такую правку значило бы легализовать её.
  const racy = acceptStand({
    seenMtime: "100",
    snapshot: { sandbox: { enabled: true } },
    fileAfter: { sandbox: { enabled: false } },
    fileMtime: "300",
  });
  racy.freshness.accept("100", "200");
  await tick(); await tick();
  assert.equal(racy.seen, "200", "отпечаток записи карточки принят");
  assert.deepEqual(racy.c, { sandbox: { enabled: true } },
    "срез принят по чужому отпечатку — перечитывание легализовало чужую правку");
}
{
  // Старая оболочка без отпечатков: принимать нечего, никто никуда не ходит.
  const old = acceptStand({ seenMtime: "100", snapshot: { sandbox: { enabled: true } } });
  old.freshness.accept(null, null);
  await tick();
  assert.deepEqual(old.calls, [], "без пары отпечатков рамка ходит в файл");
}

// --------------------------------------------------------------------------
// 3. Карточка режима: ограда с оградой, а не ступень с оградой.
// --------------------------------------------------------------------------
assert.ok(modeSource.includes("const pickedFence = picked === \"session0\" ? fencePicked : picked;"),
  "syncPlan снова сравнивает ступень лестницы с оградой трубы — вечный «ограда сменится»");
assert.ok(modeSource.includes("if (pickedFence === live.name) {"),
  "план «ограда сменится после сохранения» не смотрит на ограду под ступенью");
assert.ok(modeSource.includes("(picked === \"session0\" ? fencePicked : picked) !== live.name"),
  "хвост расписки note() снова сравнивает ступень с оградой — вечная кнопка перезапуска");
assert.ok(!modeSource.includes("picked === live.name"),
  "вернулось прямое сравнение ступени с оградой в syncPlan");
assert.ok(!modeSource.includes("picked !== live.name"),
  "вернулось прямое сравнение ступени с оградой в note()");

// --------------------------------------------------------------------------
// 4. Проводка рамки — якоря на код (образец: settings-firstrun.test.mjs).
// --------------------------------------------------------------------------
assert.ok(frameSource.includes("const restartBlocks = blocksNeedingRestart(c, out);"),
  "расписка «Сохранить» перестала сверять блоки перезапуска");
{
  const accept = frameSource.slice(acceptFrom, acceptTill);
  assert.ok(accept.includes("String(r.mtime_ns) === seenMtime"),
    "перечитывание среза не сверяет отпечаток — примет чужую правку");
  assert.ok(accept.includes("c = r.config"),
    "принятая запись карточки не тянет срез конфига рамки вперёд — вечный хвост «перезапуском»");
  assert.ok(accept.includes("base === seenMtime") && accept.includes("seenMtime = fresh"),
    "рамка не сверяет базу отпечатка (ревью 06.10, P1)");
}

rmSync(standDir, { recursive: true, force: true });
console.log("settings-step-save: OK — своя запись ступени не рождает хвост про перезапуск");
