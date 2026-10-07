import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
const code = readFileSync(new URL("../../ui-kit/window/session.ts", import.meta.url), "utf8");
const { engineOperationObserved, engineWords, activityWords, sameRoom, interruptReceiptMatches } =
  await import("data:text/javascript;base64," + Buffer.from(stripTypeScriptTypes(code)).toString("base64"));
const owner = { supported: true, stopped: false, runner_alive: true, pid: 100, agent_id: "one" };
const op = { action: "restart", accepted: true, previousPid: 100, started: 0, agent_id: "one" };
assert.equal(engineOperationObserved(op, owner, true, true, 100), false, "accepted request is not a restart");
assert.equal(engineOperationObserved(op, owner, false, true, 101), false, "channel must recover");
assert.equal(engineOperationObserved(op, owner, true, false, 101), false, "API alone is not an agent");
assert.equal(engineOperationObserved(op, owner, true, true, 101), false, "native observation must match API pid");
assert.equal(engineOperationObserved(op, {...owner, pid:101}, true, true, 101), true);
assert.equal(engineOperationObserved({...op, acceptedAt:200}, {...owner, pid:101}, true, true, 101, 199), false, "late reply to an old query is stale");
assert.equal(engineOperationObserved({...op, acceptedAt:200}, {...owner, pid:101}, true, true, 101, 201), true);
assert.equal(engineOperationObserved({...op, action:'resume'}, {...owner, runner_alive:false}, true, true, 100), false, "stale green API is not a resume");
assert.equal(engineOperationObserved(op, {...owner, agent_id: "other"}, true, true, 101), false);
assert.equal(engineOperationObserved({...op, accepted: false}, owner, true, true, 101), false);
const stop = { ...op, action: "stop" };
assert.equal(engineOperationObserved(stop, {...owner, stopped: true}, false, false, 100), false, "marker is not teardown");
assert.equal(engineOperationObserved(stop, {...owner, stopped: true, runner_alive: null}, false, false, 100), false);
assert.equal(engineOperationObserved(stop, {...owner, stopped: true, runner_alive: false}, false, false, 100), true);
assert.equal(engineOperationObserved({...op, action: "resume"}, {...owner, stopped: true}, true, true, 101), false);
assert.equal(engineWords({...owner, stopped: true}, null), "Останавливается…");
assert.equal(engineWords({...owner, stopped: true, runner_alive: false}, null), "Движок остановлен");
// 06.10: restart не мгновенен — движок выходит на границу текущего хода, и
// слово обязано это говорить, а не висеть молчаливым «Перезапускается…».
assert.equal(engineWords(owner, {...op, action: "restart"}), "Перезапускается — ждёт границы текущего хода");
assert.equal(engineWords(owner, {...op, action: "stop"}), "Останавливается…");
assert.equal(engineWords(owner, {...op, action: "resume"}), "Запускается…");
// 1.4.1, живой случай 06.10: снятый агент при служебной установке — слова
// объясняют состояние и кто его изменит, вместо молчаливой «горящей» кнопки.
assert.equal(engineWords({...owner, enabled: false, service: true}, null),
  "Агент снят в настройках; его держит служба — отпустит перезапуск службы («Система» → «Перезапустить»)");
assert.equal(engineWords({...owner, enabled: false, service: false}, null),
  "Агент снят в настройках — движок не поднимется, пока не вернёшь галочку");
// стоп-кран сильнее снятия: stopped=true говорит своё слово, даже если enabled=false
assert.equal(engineWords({...owner, enabled: false, stopped: true, runner_alive: false}, null), "Движок остановлен");
// старая оболочка без полей — прежнее поведение, никаких новых слов
assert.equal(engineWords({...owner}, null), "");
assert.equal(activityWords(null), "");
assert.equal(activityWords({phase: "model"}), "Думает...");
assert.equal(activityWords({phase: "tool"}), "Работает...");
assert.equal(sameRoom("window", "legacy", "window", "legacy"), true);
assert.equal(sameRoom("window-a", "window-b", "window", "legacy"), false);
assert.equal(interruptReceiptMatches("new", {id: "old", cancelled: ["same-run"]}), false);
assert.equal(interruptReceiptMatches("new", {id: "new", pending_tool_outcomes: ["run"]}), true);
console.log("session: observed lifecycle, phase and stale receipt contracts OK");
