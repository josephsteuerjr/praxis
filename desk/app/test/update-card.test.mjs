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

// Текст из расписки — не разметка.
{
  const receipt = { id: "\"><img src=x>", state: "awaiting", nonce: "<b>", plan: { reason: "<script>alert(1)</script>" } };
  const html = updateCardHTML({ updater: beat, receipt }, { inContainer: true });
  assert.doesNotMatch(html, /<script>/);
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;script&gt;/);
}

console.log("update-card: ok");
