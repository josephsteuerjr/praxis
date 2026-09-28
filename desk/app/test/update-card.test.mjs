// Карточка «Обновление» экрана «Система» (27.09): окно к серверу, исполнитель рядом.
//
// «Всё максимально просто» (Егор, 27.09): владелец — не обязательно айтишник. Правило живёт
// в ui-kit/window/views/update-card.ts как чистая функция — здесь проверяется то, что может
// соврать или запутать:
//   * «Обновить до X» — одна кнопка (она и есть «да»); подтверждения следом нет;
//   * агент просит сам — «Обновить» / «Не сейчас» с id и ключом его расписки;
//   * итог — одной фразой (`summary`), технические подробности — под «Подробнее»;
//   * без исполнителя на сервере — одна команда словами, без кнопок; на Windows — пусто;
//   * отказ «мало места» предлагает обновиться без копии памяти той же кнопкой;
//   * канал молчит — не «исполнителя нет»;
//   * чужой текст из расписки не становится разметкой.
//
// Запуск: node app/test/update-card.test.mjs (из корня desk/ или из app/).

import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";

const views = new URL("../../ui-kit/window/views/", import.meta.url);
const moduleURL = (source) => "data:text/javascript;base64," + Buffer.from(stripTypeScriptTypes(source)).toString("base64");
const { updateCardHTML, updateActive, deskTrialHTML } = await import(moduleURL(readFileSync(new URL("update-card.ts", views), "utf8")));

const beat = { present: true, alive: true, ok: true, why: "", current: "1.1.1", latest: { version: "1.1.2" }, newer: true };
/** Что видно, пока «Подробнее» свёрнуто. */
const visible = (html) => html.replace(/<details[\s\S]*?<\/details>/g, "");

// Windows/Mac без исполнителя — раздела нет вовсе.
assert.equal(updateCardHTML(null, { inContainer: false }), "");
assert.equal(updateCardHTML({ updater: { present: false }, receipt: null }, { inContainer: false }), "");

// Сервер без исполнителя — одна команда словами, без кнопок.
{
  const html = updateCardHTML({ updater: { present: false, command: "sh server/install.sh" }, receipt: null }, { inContainer: true });
  assert.match(html, /sh server\/install\.sh/);
  assert.match(html, /одной кнопкой/);
  assert.doesNotMatch(html, /<button/);
  assert.doesNotMatch(html, /docker compose/);
}

// Есть новее и ничего не идёт — одна кнопка «Обновить до X», подтверждения нет.
{
  const html = updateCardHTML({ updater: beat, receipt: null }, { inContainer: true });
  assert.match(html, /Есть новая версия 1\.1\.2/);
  assert.match(html, /data-update-plan="1\.1\.2" data-backup="full">Обновить до 1\.1\.2</);
  assert.match(html, /память агента не трогается/);
  assert.doesNotMatch(html, /data-update-confirm/);
  assert.equal(updateActive({ updater: beat, receipt: null }), false);
  const latest = updateCardHTML({ updater: { ...beat, newer: false, current: "1.1.2" }, receipt: null }, { inContainer: true });
  assert.match(latest, /Стоит 1\.1\.2 — это последняя версия/);
  assert.doesNotMatch(latest, /<button/);
}

// Агент просит обновиться сам: «Обновить» / «Не сейчас» — с id и ключом; подробности свёрнуты.
{
  const receipt = {
    id: "aaaa1111", state: "awaiting", nonce: "n0nce", from_version: "1.1.1", to_version: "1.1.2",
    release: { size: 260 * 1024 * 1024, sha256: "a".repeat(64), notes: "Чинит голос" },
    backup: { words: "код, helene.json и вся папка data/", data_bytes: 3 * 1024 ** 3 },
    plan: { asked_by: "agent", reason: "владелец просил голос" },
    awaiting_until_utc: "2026-09-28T10:00:00Z",
    code_preview: { mounted: true, base: true, edited: 2, files: ["tree/agent.py", "tree/yono.py"],
      note: "агент правил свой код: 2 файл(ов) — перенесу правки на новую версию, что не ляжет — отдам ему" },
  };
  const html = updateCardHTML({ updater: beat, receipt }, { inContainer: true, now: Date.parse("2026-09-27T10:00:00Z") });
  assert.match(visible(html), /Агент просит обновиться до 1\.1\.2/);
  assert.match(visible(html), /«владелец просил голос»/);
  assert.match(html, /data-update-confirm="yes" data-id="aaaa1111" data-nonce="n0nce">Обновить</);
  assert.match(html, /data-update-confirm="no" data-id="aaaa1111" data-nonce="n0nce">Не сейчас</);
  // технические подробности — есть, но свёрнуты
  for (const detail of [/1\.1\.1 → 1\.1\.2/, /260 МБ/, /aaaaaaaaaaaaaaaa…/, /3\.0 ГБ/, /через 24 ч/, /tree\/agent\.py, tree\/yono\.py/]) {
    assert.match(html, detail);
    assert.doesNotMatch(visible(html), detail);
  }
  // пока просьба ждёт, второй «Обновить до X» не рисуется — и окно следит часто
  assert.doesNotMatch(html, /data-update-plan/);
  assert.equal(updateActive({ updater: beat, receipt }), true);
}

// Идёт — шаг словами, кнопок нет; сделанное — под «Что уже сделано».
{
  const receipt = { id: "a", state: "running", from_version: "1.1.1", to_version: "1.1.2", step: "готовлю новую версию — это несколько минут",
    steps: [{ at: "t", step: "архив скачался целым", ok: true }, { at: "t", step: "готовлю новую версию — это несколько минут", ok: true }] };
  const html = updateCardHTML({ updater: beat, receipt }, { inContainer: true });
  assert.match(visible(html), /Идёт обновление до 1\.1\.2/);
  assert.match(visible(html), /готовлю новую версию — это несколько минут/);
  assert.match(html, /<summary>Что уже сделано<\/summary>[\s\S]*архив скачался целым/);
  assert.doesNotMatch(html, /<button/);
}

// Откат — фраза для человека сверху, проверки и откат — в подробностях.
{
  const receipt = { id: "a", state: "rolled_back", from_version: "1.1.1", to_version: "1.1.2", note: "1.1.2 не прошла (проверки не прошли — агент (раннер) жив: не отвечает) — вернул 1.1.1",
    summary: "Не получилось — вернул прежнюю версию 1.1.1. Почему: новая версия не заработала: агент не отвечает. Память и настройки агента целы.",
    checks: [{ name: "runner", title: "агент (раннер) жив", ok: false, note: "не отвечает" }],
    rollback: { ok: true, notes: ["прежний код на месте", "данные агента не трогал"] } };
  const html = updateCardHTML({ updater: { ...beat, newer: false }, receipt }, { inContainer: true });
  assert.match(visible(html), /class="receipt err">Не получилось — вернул прежнюю версию 1\.1\.1\. Почему: новая версия не заработала: агент не отвечает/);
  assert.doesNotMatch(visible(html), /раннер/);
  assert.match(html, /✗<\/span> агент \(раннер\) жив — не отвечает/);
  assert.match(html, /Откат: прежний код на месте; данные агента не трогал; прежняя версия поднята и прошла проверки/);
}

// Мало места — та же кнопка предлагает обновиться без копии памяти.
{
  const receipt = { id: "a", state: "refused", note: "мало места: нужно 4 ГБ, свободно 1 ГБ. План с копией только кода (backup: code) займёт меньше.",
    summary: "На диске сервера мало места: нужно около 4 ГБ, свободно 1 ГБ. Можно обновиться без копии памяти агента — кнопка ниже." };
  const html = updateCardHTML({ updater: beat, receipt }, { inContainer: true });
  assert.match(html, /data-backup="code">Обновить до 1\.1\.2 без копии памяти</);
  assert.match(visible(html), /мало места/);
}

// Исполнитель болен — причина вместо кнопок.
{
  const html = updateCardHTML({ updater: { ...beat, ok: false, why: "подними меня с HELENE_DIR" }, receipt: null }, { inContainer: true });
  assert.match(html, /подними меня с HELENE_DIR/);
  assert.doesNotMatch(html, /<button/);
}

// Правки агента не легли на новую версию — видно в испытании (свёрнуто) и в итоге.
{
  const receipt = { id: "aaaa1111", state: "trial", from_version: "1.1.1", to_version: "1.1.2",
    checks: [{ name: "running", ok: true }, { name: "runner", ok: true }],
    trial: { key: "k3y", until_utc: "2026-09-27T10:30:00Z", minutes: 30 },
    agent_code: { mounted: true, edited: ["tree/agent.py", "tree/x.py"], carried: ["tree/x.py"], merged: [],
      conflicts: [{ path: "tree/agent.py", why: "одни строки" }], folder: "workspace/update-1.1.2",
      summary: "правок агента в коде: 2; перенесено 1, слито 0, не легло 1" } };
  const u = { updater: beat, receipt };
  const html = updateCardHTML(u, { inContainer: true, now: Date.parse("2026-09-27T10:12:00Z") });
  assert.match(visible(html), /Обновлено до 1\.1\.2 — агент проверяет себя/);
  assert.match(visible(html), /Делать ничего не нужно \(срок — через 18 мин\)/);
  assert.match(html, /data-update-verdict="accept" data-id="aaaa1111" data-key="k3y">Всё хорошо</);
  assert.match(html, /data-update-verdict="reject" data-id="aaaa1111" data-key="k3y">Вернуть прежнюю</);
  assert.match(html, /Не легло: tree\/agent\.py/);
  assert.match(html, /workspace\/update-1\.1\.2/);
  assert.match(html, /память агента остаётся/);
  assert.doesNotMatch(html, /data-update-plan/);
  assert.equal(updateActive(u), true);
}

// Итог: фраза для человека; слово на испытании и судьба правок — в подробностях.
{
  const receipt = { id: "a", state: "done", from_version: "1.1.1", to_version: "1.1.2", note: "1.1.1 → 1.1.2: проверено …",
    summary: "Готово: теперь стоит 1.1.2. Агент проверил себя в новой версии — всё работает.",
    trial: { verdict: { verdict: "accept", by: "agent", words: "руки живы" } },
    agent_code: { mounted: true, edited: ["tree/x.py"], carried: ["tree/x.py"], conflicts: [],
      summary: "правок агента в коде: 1; перенесено 1, слито 0, не легло 0" } };
  const html = updateCardHTML({ updater: { ...beat, newer: false, current: "1.1.2" }, receipt }, { inContainer: true });
  assert.match(visible(html), /class="receipt ok">Готово: теперь стоит 1\.1\.2\. Агент проверил себя/);
  assert.match(html, /Испытание: агент — «принимаю»: руки живы/);
  assert.match(html, /Правки агента в коде — 1 файл\(ов\): перенесено 1, слито 0, не легло 0/);
  const quiet = updateCardHTML({ updater: beat, receipt: { ...receipt, state: "rolled_back",
    trial: { verdict: { verdict: "timeout" } } } }, { inContainer: true });
  assert.match(quiet, /агент не ответил до срока/);
  // старая расписка без summary — показывается её note
  const old = updateCardHTML({ updater: beat, receipt: { id: "b", state: "done", note: "прошло по-старому" } }, { inContainer: true });
  assert.match(visible(old), /прошло по-старому/);
}

// Канал молчит посреди подмены — это не «исполнителя нет» (ревью 27.09).
{
  const receipt = { id: "a", state: "running", from_version: "1.1.1", to_version: "1.1.2",
    step: "останавливаю агента на несколько минут", steps: [{ at: "t", step: "останавливаю агента на несколько минут", ok: true }] };
  const html = updateCardHTML({ updater: beat, receipt }, { inContainer: true, offline: true });
  assert.match(html, /Канал не отвечает/);
  assert.match(html, /посреди обновления это ожидаемо/);
  assert.match(html, /останавливаю агента/);
  assert.doesNotMatch(html, /<button/);
  assert.doesNotMatch(html, /install\.sh|up -d --build/);
  const blank = updateCardHTML(null, { inContainer: true, offline: true });
  assert.match(blank, /Канал не отвечает/);
  assert.doesNotMatch(blank, /install\.sh/);
  assert.equal(updateCardHTML(null, { inContainer: false, offline: true }), "");
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
  const summary = updateCardHTML({ updater: beat, receipt: { id: "a", state: "done", summary: "<img src=x onerror=1>" } },
    { inContainer: true });
  assert.doesNotMatch(summary, /<img/);
}

// ПК (1.2.5): испытание ведёт установщик — кнопки владельца поверх слова агента, в Настройках.
{
  const now = Date.parse("2026-09-29T10:00:00Z");
  const trial = { id: "d1", desktop: true, state: "trial", from_version: "1.2.4", to_version: "1.2.5",
    checks: [{ name: "runner", ok: true }], trial: { key: "kk", until_utc: "2026-09-29T10:30:00Z" } };
  const html = deskTrialHTML({ updater: { present: true }, receipt: trial }, { now });
  assert.match(html, /data-update-verdict="accept" data-id="d1" data-key="kk">Всё хорошо</);
  assert.match(html, /data-update-verdict="reject" data-id="d1" data-key="kk">Вернуть прежнюю</);
  assert.match(html, /прежняя программа/);
  assert.doesNotMatch(html, /код и образ/);
  // расписка сервера — не здесь; итог — три дня, потом пусто
  assert.equal(deskTrialHTML({ receipt: { ...trial, desktop: false } }, { now }), "");
  const done = { ...trial, state: "done", summary: "обновление до 1.2.5 прошло", finished_utc: "2026-09-28T10:00:00Z" };
  assert.match(deskTrialHTML({ receipt: done }, { now }), /обновление до 1\.2\.5 прошло/);
  assert.equal(deskTrialHTML({ receipt: { ...done, finished_utc: "2026-09-20T10:00:00Z" } }, { now }), "");
  assert.equal(deskTrialHTML(null), "");
  // «Система» на ПК серверный раздел не рисует: там он говорил бы про «исполнителя» и
  // «последнюю версию» (ревью 29.09) — испытание ПК живёт в Настройках.
  assert.equal(updateCardHTML({ updater: { present: true, ok: true, desktop: true }, receipt: trial }, { inContainer: false }), "");
  assert.equal(updateCardHTML({ updater: { present: false }, receipt: done }, { inContainer: false }), "");
}

console.log("update-card: ok");
