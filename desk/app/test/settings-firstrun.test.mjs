// Первый запуск без объяснения — как это было на Linux 03.10: человек встал перед
// формой, не вставил ключ модели, движок не поднялся, и окно сказало «нет связи
// с агентом» вместо «вставь ключ». Стенд держит три обещания экрана настроек:
//
//   1. на первом запуске экран ГОВОРИТ, что без ключа модели движок не встанет;
//   2. «Сохранить» с пустым ключом не обещает «впервые запустить агента» —
//      это было бы ложью: движок поднимется, а агент без мозга молчит;
//   3. обещание «перезапусти, чтобы впервые запустить» живёт только в ветке
//      с непустым ключом.
//
// Запуск: node app/test/settings-firstrun.test.mjs (из корня desk/ или из app/).

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { strict as assert } from "node:assert";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "..", "ui-kit", "window", "views", "settings-frame.ts"), "utf8");

/** Комментарии — не код: слова разбираем из кода, а не из пояснений к нему. */
const code = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
const bare = code(source);

let failures = 0;
const check = (name, ok) => { console.log(`${ok ? "ok" : "FAIL"}: ${name}`); if (!ok) failures++; };

// 1. Баннер первого запуска: существует, привязан к needs_local_setup и называет
//    причину («без ключа модели движок не запускается») и место («Модель»).
check("баннер первого запуска привязан к needs_local_setup",
  /if \(cfg\.needs_local_setup\)\s*\{\s*center\.append\(el\("div", "notice"/.test(bare));
check("баннер называет ключ модели причиной",
  /notice[\s\S]{0,400}Без ключа модели движок не запускается/.test(bare));
check("баннер называет карточку «Модель»",
  /notice[\s\S]{0,400}карточку «Модель»/.test(bare));

// 2. Пустой ключ при первом запуске — ошибка вслух, а не успех.
check("ветка пустого ключа на первом запуске существует",
  /cfg\.needs_local_setup && !String\(out\.model\?\.key \|\| ""\)\.trim\(\)/.test(bare));
{
  const branch = bare.split("cfg.needs_local_setup && !String(out.model?.key").pop()?.split("} else {")[0] ?? "";
  check("пустой ключ: расписка — ошибка (receipt err)", branch.includes('"receipt err"'));
  check("пустой ключ: кнопка перезапуска спрятана", branch.includes("restartBtn.hidden = true"));
  check("пустой ключ: текст без «впервые запустить»", !branch.includes("впервые запустить"));
  check("пустой ключ: текст называет причину", branch.includes("ключ модели пуст"));
}

// 3. Обещание «впервые запустить агента» — только в тропе с ключом.
{
  const filled = bare.split("if (cfg.needs_local_setup) saveOut.textContent")[1]?.split("\n")[0] ?? "";
  check("тропа с ключом обещает первый запуск", filled.includes("впервые запустить агента"));
}

console.log(failures ? `\nКРАСНЫХ: ${failures}` : "\nвсе зелёные");
process.exitCode = failures ? 1 : 0;
