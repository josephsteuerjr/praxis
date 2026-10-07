// Материалы леджера — полоски-диаграмма в бумажном стиле (слово владельца
// 05.10: «не выглядит диаграммой», «находится в жопе», «никак не чистится и
// не разворачивается»). Стенд исполняет ЖИВОЙ materialsHTML из anatomy.ts с
// подставными esc/fmtN (как modecard-service.test.mjs исполняет секцию службы)
// и проверяет проводку якорями: уедет строка — покраснеет, а не замолчит.
//
// Запуск: node app/test/materials-strip.test.mjs (из корня desk/ или из app/).

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { strict as assert } from "node:assert";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "..", "ui-kit", "window", "views", "anatomy.ts"), "utf8");

// --- вырезаем блок материалов: от словаря классов до INTRO.
const FROM = "const RETENTION_CLASS_RU";
const TILL = "const INTRO";
const from = source.indexOf(FROM);
const till = source.indexOf(TILL, from);
assert.ok(from > 0 && till > from, "блок материалов не нашёлся в anatomy.ts — стенд отстал от файла");
const section = source.slice(from, till);

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
const fmtN = (n) => String(n);

// Два TS-оборуда в этом блоке снимаем точечно; уедет сигнатура — стенд упадёт
// ниже по содержимым якорям, а не молча.
const js = section
  .replace("const RETENTION_CLASS_RU: Record<string, string> =", "const RETENTION_CLASS_RU =")
  .replace("function fmtMB(bytes: number): string {", "function fmtMB(bytes) {")
  .replace("function materialsHTML(r: RetentionReport | null): string {", "function materialsHTML(r) {");
assert.ok(!/[A-Za-z]\w*\s*:\s*(number|string|Record|RetentionReport)/.test(js),
  "в блоке материалов появились новые TS-типы — обнови снятие типов в стенде");

// eslint-disable-next-line no-new-func
const materialsHTML = new Function("esc", "fmtN", `${js}\nreturn materialsHTML;`)(esc, fmtN);

const kb = (n) => n * 1024;
const entries = [
  { class: "project", path: "C:\\d\\workspace\\big-project", bytes: kb(1000), files: 40, age_days: 6 },
  { class: "project", path: "C:\\d\\workspace\\tiny", bytes: kb(10), files: 2, age_days: 1 },
  { class: "models", path: "C:\\d\\models", bytes: kb(500), files: 5, age_days: 30 },
  { class: "update_staging", path: "C:\\d\\workspace\\update-1.2.6", bytes: kb(100), files: 3, age_days: 20, sweepable: true },
];

// --------------------------------------------------------------------------
// 1. Диаграмма: заливка каждой полоски — доля веса; самой тяжёлой — вся ширина.
// --------------------------------------------------------------------------
{
  const html = materialsHTML({ entries });
  const fills = [...html.matchAll(/--fill:(\d+)%/g)].map((m) => Number(m[1]));
  assert.equal(fills.length, entries.length, "у каждой записи должна быть заливка");
  // порядок — по убыванию веса: 1000 КБ → 500 → 100 → 10
  assert.equal(fills[0], 100, "самая тяжёлая запись тянется на всю ширину");
  assert.ok(fills[1] === 50 && fills[2] === 10 && fills[3] === 2,
    `доли по весу: 500→${fills[1]}, 100→${fills[2]}, 10→${fills[3]} (минимум — заметная полоска)`);
}

// --------------------------------------------------------------------------
// 2. Чистится: у проектов — «Удалить папку», у моделей — нет; sweep-мусор — класс.
// --------------------------------------------------------------------------
{
  const html = materialsHTML({ entries });
  assert.ok(html.includes('data-retention-delete="C:\\d\\workspace\\big-project"'.replace(/&/g, "&amp;")),
    "у проекта нет кнопки удаления — «никак не чистится» вернулось");
  const modelsAt = html.indexOf('data-path="C:\\d\\models"');
  const modelsBlock = html.slice(modelsAt, html.indexOf("</details>", modelsAt));
  assert.ok(modelsAt > 0 && !modelsBlock.includes("data-retention-delete"),
    "модели удаляются кнопкой — а не должны");
  assert.ok(html.includes('material-strip sweep'), "sweep-мусор не отмечен густой заливкой");
  assert.ok(html.includes("Убрать мусор обновлений"), "кнопка уборки мусора пропала");
}

// --------------------------------------------------------------------------
// 3. Пустой леджер честно называется пустым.
// --------------------------------------------------------------------------
assert.ok(materialsHTML({ entries: [] }).includes("Леджер ретенции пуст"));
assert.ok(materialsHTML(null).includes("Леджер ретенции пуст"));

// --------------------------------------------------------------------------
// 4. Проводка экрана: место наверху, разворот и удаление — явными обработчиками.
// --------------------------------------------------------------------------
assert.ok(source.includes("${meta}${materials}${modeBox}${fenceBox}"),
  "материалы снова уехали в низ экрана — слово владельца 05.10: «находится в жопе»");
assert.ok(source.includes("e.preventDefault();\n      strip.open = !strip.open;"),
  "нативный toggle вернулся — у владельца он не открывался (05.10)");
assert.ok(source.includes('{ action: "delete", path: btn.dataset.retentionDelete || "" }'),
  "кнопка удаления не подключена к ручке канала");
assert.ok(source.includes('btn.dataset.armed === "1"'),
  "удаление без подтверждения вторым щелчком — случайный клик сотрёт папку");

console.log("materials-strip: OK — диаграмма, чистка и разворот на месте");
