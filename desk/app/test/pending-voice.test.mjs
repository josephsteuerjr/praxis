// Пузырь «отправляется…» против ленты: голосовое подтверждается расшифровкой.
//
// ⚠ 26.09, живой случай у мамы Егора: четыре пузыря «[голосовое] · агент читает
// сейчас» висели под уже отвеченными репликами до перезапуска окна. Лента несёт
// «[голосовое]: расшифровка» (или «[голосовое не расшифровано: …]»), пузырь —
// заглушку «[голосовое]», и сверка слово в слово не сходилась никогда. Правило
// живёт в ui-kit/window/pending.ts как чистая функция — здесь оно проверяется.
//
// Запуск: node app/test/pending-voice.test.mjs (из корня desk/ или из app/).

import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";

const kit = new URL("../../ui-kit/window/", import.meta.url);
const moduleURL = (source) => "data:text/javascript;base64," + Buffer.from(stripTypeScriptTypes(source)).toString("base64");
const { confirmedByFeed } = await import(moduleURL(readFileSync(new URL("pending.ts", kit), "utf8")));

const at = "2026-09-26T14:32:00.000Z";
const later = "2026-09-26T14:32:09.000Z";
const earlier = "2026-09-26T14:20:00.000Z";

// Текст слово в слово — как раньше.
assert.equal(confirmedByFeed({ text: "привет", at }, [{ text: "привет", timestamp: later }]), true);
assert.equal(confirmedByFeed({ text: "привет", at }, [{ text: "пока", timestamp: later }]), false);

// Голосовое: лента несёт расшифровку — это тот же пузырь.
assert.equal(confirmedByFeed({ text: "[голосовое]", at }, [{ text: "[голосовое]: А разве ты не видишь?", timestamp: later }]), true);
assert.equal(confirmedByFeed({ text: "[голосовое]", at }, [{ text: "[голосовое не расшифровано: слух не поднят]", timestamp: later }]), true);
assert.equal(confirmedByFeed({ text: "[голосовое]", at }, [{ text: "[голосовое: расшифровка пустая — тишина]", timestamp: later }]), true);
// Картинка — то же правило.
assert.equal(confirmedByFeed({ text: "[картинка]", at }, [{ text: "[картинка] · photo.png", timestamp: later }]), true);

// Своя старая голосовая не подтверждает новый пузырь: время — часть правила.
assert.equal(confirmedByFeed({ text: "[голосовое]", at }, [{ text: "[голосовое]: вчерашнее", timestamp: earlier }]), false);
// Часы окна и канала могут разойтись на секунды — пять секунд назад ещё считается.
assert.equal(confirmedByFeed({ text: "[голосовое]", at }, [{ text: "[голосовое]: почти вовремя", timestamp: "2026-09-26T14:31:57.000Z" }]), true);
// Строка без времени (старый канал) — подтверждает по началу текста.
assert.equal(confirmedByFeed({ text: "[голосовое]", at }, [{ text: "[голосовое]: без времени" }]), true);
// Обычный текст по началу НЕ подтверждается: «при» ≠ «привет».
assert.equal(confirmedByFeed({ text: "при", at }, [{ text: "привет", timestamp: later }]), false);

console.log("pending-voice: ok");
