// Правила монтирования на стороне окна — прогоном, а не чтением исходника.
//
// Экран монтирования читает и пишет `sandbox.mounts` и `sandbox.mounts_denied`,
// то есть ровно тот список, ради которого песочница вообще имеет смысл. Здесь
// проверено, что окно понимает файл ТАК ЖЕ, как его понимает харнесс
// (localharness/fence.py: `parse_mounts`, `parse_denied`, `mount_access`), и не
// теряет чужие поля: этот файл владелец правит ещё и руками.
//
// Запуск: node app/test/mounts.test.mjs (из корня desk/ или из app/).

import { strict as assert } from "node:assert";

import { mountAccess, mountKey, parseDenied, parseMounts, pathProblem, stamp } from "../src/mountdata.ts";

// --- 1. формы строки, которые допускает файл ------------------------------- #
// fence.parse_mounts принимает три формы, потому что файл пишут руками. Окно
// обязано понимать все три, иначе владелец смонтировал папку, а в списке её нет.
const rows = parseMounts([
  "C:\\Users\\Егор\\Документы",
  { path: "D:\\Работа", access: "write" },
  { path: "E:\\Архив", write: true },
  { path: "F:\\Фото", access: "чушь" },
]);
assert.equal(rows.length, 4);
assert.equal(rows[0].access, "read", "строкой — это чтение");
assert.equal(rows[1].access, "write");
assert.equal(rows[2].access, "write", "форма {write: true} тоже понятна — её пишет рука владельца");
assert.equal(rows[3].access, "read", "незнакомое слово доступа = чтение: опечатка не должна давать запись");

// Второе имя одной ручки не уезжает обратно в файл: харнесс читает `access`
// первым, и оставленный рядом `write: true` был бы второй правдой.
assert.ok(!("write" in rows[2]), "рядом с access остался write — две правды об одной папке");

// --- 2. чужие поля переживают экран ---------------------------------------- #
const kept = parseMounts([{ path: "C:\\Проект", access: "read", why: "смотреть сметы", at: "01.09.2026 09:00", tag: "своё" }]);
assert.equal(kept[0].why, "смотреть сметы");
assert.equal(kept[0].at, "01.09.2026 09:00");
assert.equal(kept[0].tag, "своё", "поле, которого окно не знает, обязано пережить экран");

// --- 3. мусор не роняет и не проходит -------------------------------------- #
assert.deepEqual(parseMounts(undefined), []);
assert.deepEqual(parseMounts("C:\\Папка"), [], "не список — читать нечего");
assert.deepEqual(parseMounts([null, 7, {}, { path: "   " }]), [], "пустой путь в список не попадает");

// Две строки на одну папку: побеждает первая — так же, как в fence.parse_mounts.
const twice = parseMounts([
  { path: "C:\\Одна", access: "read" },
  { path: "c:\\одна\\", access: "write" },
]);
assert.equal(twice.length, 1, "дубль папки не свёрнут: владелец увидит её в списке дважды");
assert.equal(twice[0].access, "read", "побеждать обязана первая строка, а не вторая");

// --- 4. ключ сравнения путей ------------------------------------------------ #
assert.equal(mountKey("C:\\Папка\\"), mountKey("c:/папка"), "регистр и хвостовой разделитель не различают папки");
assert.equal(mountKey('  "C:\\Папка"  '), mountKey("C:\\Папка"), "кавычки из проводника не должны делать вторую папку");
assert.notEqual(mountKey("C:\\Папка"), mountKey("C:\\Папка2"));

// --- 5. отказы владельца ---------------------------------------------------- #
const denied = parseDenied(["C:\\Windows", { path: "C:\\Секрет", why: "там пароли", at: "02.09.2026 12:00" }]);
assert.equal(denied.length, 2);
assert.equal(denied[1].why, "там пароли");

// --- 6. заведомо негодный ввод отбивается сразу ----------------------------- #
// Годность пути решает харнесс (`fence.mount_refusal`: системная папка, корень
// диска, папка самой Hélène) — окно своей копии этих правил не заводит и здесь
// её не проверяет. Ловим только то, что писать в файл нельзя вовсе.
assert.ok(pathProblem(""), "пустой путь обязан отбиваться");
assert.ok(pathProblem("Документы"), "относительный путь обязан отбиваться");
assert.equal(pathProblem("C:\\Users\\Егор\\Документы"), "");
assert.equal(pathProblem("\\\\сервер\\общая"), "", "сетевая папка — полный путь");
assert.equal(pathProblem("%USERPROFILE%\\Documents"), "", "переменную среды харнесс разворачивает сам");
assert.equal(pathProblem("~/Документы"), "", "тильду харнесс разворачивает сам");
// Агент на macOS (порт 0.7.1): полный путь — от корня или от дома, буквы диска
// там не бывает; без слова системы правила остаются виндовыми, как были.
assert.equal(pathProblem("/Users/yegor/Documents", "macos"), "");
assert.equal(pathProblem("~/Documents", "macos"), "");
assert.ok(pathProblem("C:\\Users\\Егор", "macos"), "буква диска на Mac — не полный путь");
assert.ok(pathProblem("Documents", "macos"), "относительный путь на Mac обязан отбиваться");
assert.ok(/\/Users\//.test(pathProblem("", "macos")), "подсказка на Mac показывает путь Mac, а не C:\\");
assert.ok(pathProblem("/Users/yegor/Documents"), "без слова системы правила виндовые: `/` — не полный путь");

// --- 7. дата в том же виде, что пишет харнесс ------------------------------- #
assert.equal(
  stamp(new Date(2026, 8, 4, 7, 5)),
  "04.09.2026 07:05",
  "дата обязана совпадать с fence._stamp_text: её читают в одном списке",
);

// --- 8. доступ ------------------------------------------------------------- #
assert.equal(mountAccess(true), "write");
assert.equal(mountAccess("чтение и запись"), "write", "владелец пишет файл по-русски — это тоже запись");
assert.equal(mountAccess(undefined), "read");

console.log("монтирование: файл читается так же, как его читает харнесс");
