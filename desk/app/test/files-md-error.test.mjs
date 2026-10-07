// Раздел «Файлы» против тихой смерти (1.4.1, живой случай 06.10 «опять не
// открываются маркдауны»): отказ дерева и отказ чтения файла обязаны стать
// словами с повтором, а не молчаливой пустотой или вечным «читаю…».
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../../ui-kit/window/views/files.ts", import.meta.url), "utf8");

// 1. Дерево (/api/md-tree) читается под ловцом: отказ → слова + «Повторить»,
//    не пустота и не необработанный throw.
const treeFetch = /let groups[^;]*;\s*\n\s*try \{\s*\n\s*groups = await api<Group\[\]>\("\/api\/md-tree"\);/.test(src);
assert.ok(treeFetch, "дерево читается под try/catch");

// 2. У отказа есть кнопка повтора, ведущая на повторный render.
assert.ok(src.includes('"Повторить"'), "кнопка «Повторить» есть");
assert.ok(/retry\.addEventListener\("click", \(\) => void render\(container\)\)/.test(src),
  "повтор перезапускает render");

// 3. Чтение файла (/api/md) тоже под ловцом: правая половина не застревает в
//    «читаю…» и не роняет весь раздел.
assert.ok(src.includes("let doc: Doc;"), "чтение файла объявляется под ловцом");
assert.ok(/doc = await api<Doc>\("\/api\/md\?path="/.test(src), "чтение идёт тем же api()");
assert.ok(/catch \(e\) \{\s*\n\s*main\.innerHTML = failHTML\(humanError\(e\)\.text/.test(src),
  "отказ чтения — словами через humanError/failHTML");

// 4. Пустое дерево остаётся честным «Файлов пока нет» — только теперь оно
//    означает живой канал, а не заглушку любого отказа.
assert.ok(src.includes("Файлов пока нет"), "пустое дерево говорит своим словом");

console.log("files-md-error: раздел Файлов говорит об отказе словами с повтором OK");
