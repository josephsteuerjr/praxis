// Карточка «Обновление» экрана «Система» (27.09): окно к серверу, исполнитель рядом.
//
// Жалоба Дмитрия К: агент на сервере не мог обновить свой контейнер изнутри. Теперь
// исполнитель снаружи сверяет план и ждёт «да» человека, а окно показывает, ЧТО именно
// поставится. Правило живёт в ui-kit/window/views/update-card.ts как чистая функция —
// здесь проверяется то, что может соврать:
//   * «Подтвердить» есть ТОЛЬКО у сверенного плана и несёт id и ключ его расписки;
//   * без исполнителя на сервере — команды словами, а кнопок нет; на Windows — пусто;
//   * «Подготовить» — только когда есть версия новее и ничего не идёт;
//   * отказ «мало места» предлагает план без копии data/ той же кнопкой;
//   * чужой текст из расписки не становится разметкой.
//
// Запуск: node app/test/update-card.test.mjs (из корня desk/ или из app/).

import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";

const views = new URL("../../ui-kit/window/views/", import.meta.url);
const moduleURL = (source) => "data:text/javascript;base64," + Buffer.from(stripTypeScriptTypes(source)).toString("base64");
const { updateCardHTML, updateActive } = await import(moduleURL(readFileSync(new URL("update-card.ts", views), "utf8")));

const beat = { present: true, alive: true, ok: true, why: "", current: "1.1.1", latest: { version: "1.1.2" }, newer: true };

// Windows/Mac без исполнителя — раздела нет вовсе.
assert.equal(updateCardHTML(null, { inContainer: false }), "");
assert.equal(updateCardHTML({ updater: { present: false }, receipt: null }, { inContainer: false }), "");

// Сервер без исполнителя — как руками и как поднять, без кнопок.
{
  const html = updateCardHTML({ updater: { present: false, command: "docker compose -f server/updater/docker-compose.yml up -d --build" }, receipt: null }, { inContainer: true });
  assert.match(html, /server\/updater\/docker-compose\.yml/);
  assert.match(html, /up -d --build/);
  assert.doesNotMatch(html, /<button/);
}

// Есть новее и ничего не идёт — «Подготовить», но не «Подтвердить».
{
  const html = updateCardHTML({ updater: beat, receipt: null }, { inContainer: true });
  assert.match(html, /data-update-plan="1\.1\.2"/);
  assert.match(html, /data-backup="full"/);
  assert.doesNotMatch(html, /data-update-confirm/);
  assert.equal(updateActive({ updater: beat, receipt: null }), false);
}

// Сверенный план ждёт «да»: версии, размер, сумма, копия — и кнопки с id и ключом.
{
  const receipt = {
    id: "aaaa1111", state: "awaiting", nonce: "n0nce", from_version: "1.1.1", to_version: "1.1.2",
    release: { size: 260 * 1024 * 1024, sha256: "a".repeat(64), notes: "Чинит голос" },
    backup: { words: "код, helene.json и вся папка data/", data_bytes: 3 * 1024 ** 3 },
    plan: { asked_by: "agent", reason: "владелец просил голос" },
    awaiting_until_utc: "2026-09-28T10:00:00Z",
  };
  const html = updateCardHTML({ updater: beat, receipt }, { inContainer: true, now: Date.parse("2026-09-27T10:00:00Z") });
  // срок — словами: «до 10:00» без даты читалось бы как «сегодня»
  assert.match(html, /без ответа план истечёт через 24 ч/);
  assert.match(html, /1\.1\.1 → 1\.1\.2/);
  assert.match(html, /260 МБ/);
  assert.match(html, /aaaaaaaaaaaaaaaa…/);
  assert.match(html, /3\.0 ГБ/);
  assert.match(html, /Просит: агент — «владелец просил голос»/);
  assert.match(html, /data-update-confirm="yes" data-id="aaaa1111" data-nonce="n0nce"/);
  assert.match(html, /data-update-confirm="no"/);
  // пока план ждёт, второго «Подготовить» нет — и окно следит часто
  assert.doesNotMatch(html, /data-update-plan/);
  assert.equal(updateActive({ updater: beat, receipt }), true);
}

// Идёт — шаги видны, кнопок нет.
{
  const receipt = { id: "a", state: "running", from_version: "1.1.1", to_version: "1.1.2", step: "собираю новый образ",
    steps: [{ at: "t", step: "сумма sha256 сошлась", ok: true }, { at: "t", step: "собираю новый образ", ok: true }] };
  const html = updateCardHTML({ updater: beat, receipt }, { inContainer: true });
  assert.match(html, /Идёт обновление 1\.1\.1 → 1\.1\.2/);
  assert.match(html, /сумма sha256 сошлась/);
  assert.doesNotMatch(html, /<button/);
}

// Откат — итог и проверки словами.
{
  const receipt = { id: "a", state: "rolled_back", from_version: "1.1.1", to_version: "1.1.2", note: "вернул 1.1.1",
    checks: [{ name: "runner", title: "агент (раннер) жив", ok: false, note: "не отвечает" }],
    rollback: { ok: true, notes: ["прежний код на месте", "data/ возвращена из копии"] } };
  const html = updateCardHTML({ updater: { ...beat, newer: false }, receipt }, { inContainer: true });
  assert.match(html, /не прошло — откачено/);
  assert.match(html, /✗<\/span> агент \(раннер\) жив — не отвечает/);
  assert.match(html, /Откат: прежний код на месте; data\/ возвращена из копии; прежняя версия поднята и прошла проверки/);
}

// Мало места — та же кнопка предлагает план без копии data/.
{
  const receipt = { id: "a", state: "refused", note: "мало места: нужно 4 ГБ, свободно 1 ГБ. План с копией только кода (backup: code) займёт меньше." };
  const html = updateCardHTML({ updater: beat, receipt }, { inContainer: true });
  assert.match(html, /data-backup="code"/);
  assert.match(html, /без копии data\//);
}

// Исполнитель болен — причина вместо кнопок.
{
  const html = updateCardHTML({ updater: { ...beat, ok: false, why: "подними меня с HELENE_DIR" }, receipt: null }, { inContainer: true });
  assert.match(html, /подними меня с HELENE_DIR/);
  assert.doesNotMatch(html, /<button/);
}

// До «да»: правки агента в его коде видны владельцу.
{
  const receipt = { id: "a", state: "awaiting", nonce: "n", from_version: "1.1.1", to_version: "1.1.2",
    code_preview: { mounted: true, base: true, edited: 2, files: ["tree/agent.py", "tree/yono.py"],
      note: "агент правил свой код: 2 файл(ов) — перенесу правки на новую версию, что не ляжет — отдам ему" } };
  const html = updateCardHTML({ updater: beat, receipt }, { inContainer: true });
  assert.match(html, /Код агента: агент правил свой код/);
  assert.match(html, /tree\/agent\.py, tree\/yono\.py/);
  const lost = updateCardHTML({ updater: beat, receipt: { ...receipt, code_preview: { mounted: false, note: "правки пропадут" } } },
    { inContainer: true });
  assert.match(lost, /class="receipt err">Код агента: правки пропадут/);
}

// Испытание: механика прошла, агент проверяет себя; у владельца — слово поверх.
{
  const receipt = { id: "aaaa1111", state: "trial", from_version: "1.1.1", to_version: "1.1.2",
    checks: [{ name: "running", ok: true }, { name: "runner", ok: true }],
    trial: { key: "k3y", until_utc: "2026-09-27T10:30:00Z", minutes: 30 },
    agent_code: { mounted: true, edited: ["tree/agent.py", "tree/x.py"], carried: ["tree/x.py"], merged: [],
      conflicts: [{ path: "tree/agent.py", why: "одни строки" }], folder: "workspace/update-1.1.2",
      summary: "правок агента в коде: 2; перенесено 1, слито 0, не легло 1" } };
  const u = { updater: beat, receipt };
  const html = updateCardHTML(u, { inContainer: true, now: Date.parse("2026-09-27T10:12:00Z") });
  assert.match(html, /Испытание: 1\.1\.2 поднята, агент проверяет себя/);
  assert.match(html, /срок — через 18 мин/);
  assert.match(html, /data-update-verdict="accept" data-id="aaaa1111" data-key="k3y"/);
  assert.match(html, /data-update-verdict="reject" data-id="aaaa1111" data-key="k3y"/);
  assert.match(html, /Не легло: tree\/agent\.py/);
  assert.match(html, /workspace\/update-1\.1\.2/);
  assert.match(html, /память агента остаётся/);
  assert.doesNotMatch(html, /data-update-plan/);
  assert.equal(updateActive(u), true);
}

// Итог: слово на испытании и судьба правок.
{
  const receipt = { id: "a", state: "done", from_version: "1.1.1", to_version: "1.1.2", note: "принято",
    trial: { verdict: { verdict: "accept", by: "agent", words: "руки живы" } },
    agent_code: { mounted: true, edited: ["tree/x.py"], carried: ["tree/x.py"], conflicts: [],
      summary: "правок агента в коде: 1; перенесено 1, слито 0, не легло 0" } };
  const html = updateCardHTML({ updater: { ...beat, newer: false }, receipt }, { inContainer: true });
  assert.match(html, /Испытание: агент — «принимаю»: руки живы/);
  assert.match(html, /Правки агента в коде — 1 файл\(ов\): перенесено 1, слито 0, не легло 0/);
  const quiet = updateCardHTML({ updater: beat, receipt: { ...receipt, state: "rolled_back",
    trial: { verdict: { verdict: "timeout" } } } }, { inContainer: true });
  assert.match(quiet, /агент не ответил до срока/);
}

// Канал молчит посреди подмены — это не «исполнителя нет» (ревью 27.09): прежняя карточка
// советовала распаковать поставку руками ровно тогда, когда это ломает идущую подмену.
{
  const receipt = { id: "a", state: "running", from_version: "1.1.1", to_version: "1.1.2",
    step: "останавливаю агента", steps: [{ at: "t", step: "останавливаю агента", ok: true }] };
  const html = updateCardHTML({ updater: beat, receipt }, { inContainer: true, offline: true });
  assert.match(html, /Канал не отвечает/);
  assert.match(html, /посреди обновления это ожидаемо/);
  assert.match(html, /останавливаю агента/);
  assert.doesNotMatch(html, /<button/);
  assert.doesNotMatch(html, /распакуй|up -d --build/);
  // ответа не было вовсе — всё равно не «исполнителя нет»
  const blank = updateCardHTML(null, { inContainer: true, offline: true });
  assert.match(blank, /Канал не отвечает/);
  assert.doesNotMatch(blank, /Исполнителя обновлений рядом с агентом нет/);
  assert.equal(updateCardHTML(null, { inContainer: false, offline: true }), "");
  // план ждал «да» — кнопок без связи нет
  const waiting = updateCardHTML({ updater: beat, receipt: { id: "a", state: "awaiting", nonce: "n" } },
    { inContainer: true, offline: true });
  assert.doesNotMatch(waiting, /data-update-confirm/);
}

// Текст из расписки — не разметка.
{
  const receipt = { id: "\"><img src=x>", state: "awaiting", nonce: "<b>", plan: { reason: "<script>alert(1)</script>" } };
  const html = updateCardHTML({ updater: beat, receipt }, { inContainer: true });
  assert.doesNotMatch(html, /<script>/);
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;script&gt;/);
}

console.log("update-card: ok");
