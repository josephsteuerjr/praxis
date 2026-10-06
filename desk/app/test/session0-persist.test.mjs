// Согласие «Разрешить права СИСТЕМЫ» — не черновик (05.10, слово владельца).
//
// На Windows выбор верхней ступени слетал при каждом перезапуске программы
// (подтверждение меняло только локальное состояние карточки), а ступень без
// службы была ЗАПЕРТА — хотя именно подтверждение, по договорённости 04.10,
// обязано ставить службу само, одним паролем администратора. Теперь: ключ
// service.session0 пишется немедленно (src/session0-persist.ts), ступень не
// заперта, сход со ступени любой оградой тоже пишет false — иначе файл молча
// держал права СИСТЕМЫ при выбранной «Песочнице» на экране (ревью 05.10).
//
// 06.10: запись карточки — не «чужой писатель». Успешная запись возвращает
// свежий отпечаток файла, карточка отдаёт его рамке экрана (onConfigWrite),
// и «Сохранить» после выбора ограды больше не падает в конфликт с самой
// программой (живая жалоба владельца «кажется, кто-то переписал ваши
// настройки»).
//
// Чистая часть испытывается на прямую (как keepBlock в config-blocks.test.mjs):
// модуль без DOM, shell подставной, сети нет. Проводка карточки — якорями по
// исходнику: уедет строка — стенд покраснеет, а не замолчит.
//
// Запуск: node app/test/session0-persist.test.mjs (из корня desk/ или из app/).

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { strict as assert } from "node:assert";

import { persistSession0 } from "../src/session0-persist.ts";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "src", "modecard.ts"), "utf8");
const frameSource = readFileSync(join(here, "..", "..", "ui-kit", "window", "views", "settings-frame.ts"), "utf8");
const agentSource = readFileSync(join(here, "..", "src", "settings-agent.ts"), "utf8");

/** Подставной мир: что лежит в файле, что сказала оболочка на запись. */
function stand({ fileConfig, saveReply, saveThrows } = {}) {
  const saves = [];
  const calls = [];
  const shell = async (name, args) => {
    calls.push(name);
    if (name === "config_load") return { config: fileConfig, mtime_ns: "111" };
    if (name === "config_save") {
      saves.push(args);
      if (saveThrows) throw saveThrows;
      return saveReply ?? { ok: true, mtime_ns: "222" };
    }
    throw new Error(`неожидали команду ${name}`);
  };
  return { shell, saves, calls };
}

const FILE = { model: { key: "sk-живой" }, service: { firewall: true }, owner: { name: "Егор" } };

// --------------------------------------------------------------------------
// 1. Согласие: один ключ дописан, всё соседнее не тронуто, отпечаток ПАРОЙ.
// --------------------------------------------------------------------------
{
  const s = stand({ fileConfig: FILE });
  const stamp = await persistSession0(s.shell, true);
  assert.deepEqual(s.calls, ["config_load", "config_save"], "ровно один заход на чтение и один на запись");
  const saved = JSON.parse(s.saves[0].config);
  assert.equal(saved.service.session0, true, "ключ верхней ступени записан");
  assert.equal(saved.service.firewall, true, "соседняя галочка службы не тронута");
  assert.equal(saved.model.key, "sk-живой", "чужие блоки конфига не тронуты");
  assert.equal(s.saves[0].mtimeNs, "111", "пишем по отпечатку, который только что прочитали");
  assert.deepEqual(stamp, { base: "111", fresh: "222" },
    "успешная запись вернула пару: по чему читали + что стало (ревью 06.10, P1)");
}

// --------------------------------------------------------------------------
// 2. Блока service не было вовсе — он создаётся, а не роняет запись.
// --------------------------------------------------------------------------
{
  const s = stand({ fileConfig: { model: { key: "k" } } });
  const stamp = await persistSession0(s.shell, false);
  const saved = JSON.parse(s.saves[0].config);
  assert.deepEqual(saved.service, { session0: false }, "блок service создан с одним ключом");
  assert.equal(stamp.fresh, "222", "свежий отпечаток возвращён и при создании блока");
}

// --------------------------------------------------------------------------
// 3. Конфликт свежести — честный отказ словами в обоих форматах оболочки:
//    объектный {ok:false} и старая строка stale:<mtime> в исключении.
// --------------------------------------------------------------------------
{
  const s = stand({ fileConfig: FILE, saveReply: { ok: false, code: "stale", error: "файл менялся" } });
  await assert.rejects(() => persistSession0(s.shell, true), /файл менялся/);
}
{
  const s = stand({ fileConfig: FILE, saveThrows: new Error("stale:1753000000000000000") });
  await assert.rejects(() => persistSession0(s.shell, true), /файл менялся, пока экран был открыт/);
}

// --------------------------------------------------------------------------
// 4. Пустого конфига нет — согласие не создаёт файл из одной галочки.
//    Старая оболочка без отпечатка в ответе — рамке уходит null, та перечитает.
// --------------------------------------------------------------------------
{
  const s = stand({ fileConfig: {} });
  await assert.rejects(() => persistSession0(s.shell, true), /не прочитались/);
  assert.deepEqual(s.saves, [], "в пустой файл писать нечего — и не писали");
}
{
  const s = stand({ fileConfig: FILE, saveReply: { ok: true } });
  const stamp = await persistSession0(s.shell, true);
  assert.deepEqual(stamp, { base: "111", fresh: null },
    "оболочка без отпечатка в ответе — fresh пуст, base честный: рамке принимать нечего");
}

// --------------------------------------------------------------------------
// 5. Проводка: согласие, сходы, очередь и отпечаток рамке — якорями по коду.
// --------------------------------------------------------------------------
assert.ok(source.includes("putSession0(true);"),
  "кнопка согласия больше не пишет ключ немедленно — выбор снова слетит при перезапуске");
assert.ok(source.includes("putSession0(false);") && source.split("putSession0(false);").length >= 3,
  "сход со ступени и выбор ограды не пишут ключ — файл солжет о согласии");
assert.ok(!source.includes('aria-disabled", "true"'),
  "ступень снова заперта без службы — а ставить службу обязано само согласие (слово владельца 04.10)");
assert.ok(source.includes("session0Want !== session0InFile"),
  "защита от лишних записей пропала — карточка будет писать файл на каждый щелчок");
assert.ok(source.includes("await persistSession0(shell, want);"),
  "записи ключа не выстроены в очередь — быстрые щелчки гонятся за одним файлом");
assert.ok(source.includes("onConfigWrite(stamp.base, stamp.fresh);"),
  "свежий отпечаток своей записи не уходит рамке — «Сохранить» назовёт её чужой и покажет конфликт (06.10)");

// --------------------------------------------------------------------------
// 6. Рамка принимает свою запись ТОЛЬКО по совпадению базы и не требует
//    перезапуска из-за ничего.
// --------------------------------------------------------------------------
assert.ok(frameSource.includes("freshness.accept = (base, fresh) =>"),
  "рамка не сверяет базу отпечатка — своя запись или легализует чужую правку, или врёт конфликтом");
assert.ok(frameSource.includes("base === seenMtime") && frameSource.includes("seenMtime = fresh"),
  "принятие fresh возможно мимо сверки с отпечатком открытия — легализация чужих правок (ревью 06.10, P1)");
assert.ok(agentSource.includes("freshness.accept?.(base, fresh)"),
  "издание не прокидывает пару отпечатков в карточку режима");
assert.ok(!frameSource.includes("Перезаписано. Чтобы применить, перезапусти программу."),
  "«Перезаписать своим» снова требует перезапуск всегда — даже без единого изменения (06.10)");
assert.ok(frameSource.includes("const restartBlocks = blocksNeedingRestart(before, out);"),
  "расписка перезаписи не смотрит на реальные изменения блоков");

console.log("session0-persist: OK — согласие пишет ключ немедленно, отпечаток парой по базе, перезапуск по делу");
