// Голос в ленте — на ВСЕХ поверхностях, а не только в окне.
//
// История: 11.09 вложение в окне стало проигрывателем, а телефон (и мини-апп,
// который делит с ним `ui-kit/phone.ts`) остался со строкой «[имя файла]».
// Ручка `/api/media` телефону при этом уже была открыта, и реплики он берёт той
// же `/api/chat/` — то есть отставал ровно интерфейс, и запись «телефону эта
// ручка тоже открыта» была обещанием, а не делом.
//
// Тест держит четыре вещи:
//   1) обе поверхности строят адрес через /api/media — и не литералом мимо него;
//   2) ключ уезжает В АДРЕСЕ: за `<audio src>` браузер идёт сам, мимо общего
//      `call`, и без ключа тег молча показал бы пустоту — то есть «не пустили»
//      читалось бы как «файла нет»;
//   3) звук рисуется `<audio controls>`, а не ссылкой;
//   4) реплика БЕЗ пути от дерева (файл снаружи) проигрывателя не получает:
//      канал такой файл не отдаёт, и обещать его адресом нельзя.
//
// Запуск: node app/test/media-surfaces.test.mjs

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { strict as assert } from "node:assert";

const here = dirname(fileURLToPath(import.meta.url));
const kit = join(here, "..", "..", "ui-kit");

/** Комментарии — не код: в них те же слова объясняются по-русски. */
const code = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

const phone = code(readFileSync(join(kit, "phone.ts"), "utf8"));
const talk = code(readFileSync(join(kit, "window", "views", "talk.ts"), "utf8"));
const api = code(readFileSync(join(kit, "window", "api.ts"), "utf8"));

for (const [name, text] of [["телефон", phone], ["окно", talk + api]]) {
  // 1. адрес строится ручкой канала
  assert.ok(/\/api\/media\?path=/.test(text), `${name}: адрес вложения обязан идти через /api/media?path=`);
  assert.ok(/encodeURIComponent\(rel\)/.test(text), `${name}: путь обязан кодироваться`);

  // 2. ключ — в адресе, потому что тег идёт за файлом сам
  assert.ok(/key=/.test(text) && /encodeURIComponent\(key\)|encodeURIComponent\(cfg\.key\)/.test(text),
    `${name}: ключ обязан уезжать в адресе вложения`);

  // 3. звук — проигрывателем
  assert.ok(/<audio controls preload="none"/.test(text), `${name}: звук обязан рисоваться <audio controls>`);
  assert.ok(/media_kind[^\n]*===\s*"audio"/.test(text), `${name}: вид вложения обязан читаться из media_kind`);

  // 4. нет пути от дерева — нет проигрывателя
  assert.ok(/if \(!rel\) return "";/.test(text), `${name}: без media_path блок обязан быть пустым`);
}

// Телефон обязан СОХРАНИТЬ прежнюю строку для файлов снаружи дерева: у них
// media_path нет вовсе, и молча терять упоминание файла нельзя.
assert.ok(/mediaBlock\(m\)\s*\n?\s*\|\|\s*\(m\.media \?/.test(phone),
  "телефон: строка «[имя]» обязана остаться запасным вариантом");

console.log("ok  вложение рисуется и в окне, и на телефоне");
