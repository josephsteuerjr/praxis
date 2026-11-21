// Физика 2111: ResizeObserver — всегда тихий перемер (ui-fix 20261121, финал).
// Пружина — только у намеренных событий: человек и новое сообщение (preserve).
import { strict as assert } from "node:assert";
import { fixture } from "./fixtures/scroller-clock.mjs";
let failures = 0, checks = 0;
function check(name, fn) {
  checks++;
  try { fn(); console.log(`ok: ${name}`); }
  catch (e) { failures++; console.error(`FAIL: ${name}: ${e.message}`); }
}

check("RO-шум без смены геометрии не трогает позицию и не будит пружину", () => {
  const h = fixture();
  h.s.scrollTo(1000, false);
  assert.equal(h.s.pos, 1000);
  h.s.ro.cb(); // строка статуса сменила вид, высоты не изменились
  assert.equal(h.s.pos, 1000);
  assert.equal(h.s.target, 1000);
  assert.equal(h.s.raf, 0, "пружина не должна запускаться");
  h.s.destroy();
});

check("рост композера (вьюпорт меньше) сдвигает позицию на дельту молча", () => {
  const h = fixture();
  h.s.scrollTo(1000, false);
  h.view.clientHeight = 688 - 24; // поле ввода выросло на строку
  h.s.ro.cb();
  // max: 3000-664=2336 против 2312; якорь сохранён: 1000+24
  assert.equal(h.s.pos, 1024, "контент должен остаться на месте");
  assert.equal(h.s.target, 1024);
  assert.equal(h.s.raf, 0, "никакой пружины при печати");
  h.s.destroy();
});

check("убыль вьюпорта назад тоже тихая, позиция возвращается", () => {
  const h = fixture();
  h.s.scrollTo(1024, false);
  h.view.clientHeight = 688 + 40;
  h.s.ro.cb();
  assert.equal(h.s.pos, 984);
  assert.equal(h.s.raf, 0);
  h.s.destroy();
});

check("прилепленный к низу остаётся внизу при росте композера, без пружины", () => {
  const h = fixture({ stick: true });
  h.s.scrollTo(h.s.max, false); // низ, pinned
  assert.equal(h.s.pinned, true);
  h.view.clientHeight = 688 - 24;
  h.s.ro.cb();
  assert.equal(h.s.pos, 3000 - 664, "должен остаться приклеенным к новому низу");
  assert.equal(h.s.pinned, true);
  assert.equal(h.s.raf, 0);
  h.s.destroy();
});

check("настоящее новое сообщение по-прежнему ведёт пружиной низ", () => {
  const h = fixture({ stick: true });
  h.s.scrollTo(h.s.max, false);
  h.s.preserve(() => { h.view.scrollHeight = 3600; });
  assert.ok(h.s.raf !== 0, "follow-пружина обязана запуститься");
  h.advance(1200);
  assert.ok(Math.abs(h.s.pos - (3600 - 688)) < 1, `низ не достигнут: ${h.s.pos}`);
  h.s.destroy();
});

check("RO-шум после preserve не сбивает летящую follow-пужину", () => {
  const h = fixture({ stick: true });
  h.s.scrollTo(h.s.max, false);
  h.s.preserve(() => { h.view.scrollHeight = 3600; });
  assert.ok(h.s.raf !== 0);
  h.s.ro.cb(); // RO доставил то же событие позже — дельты уже нет
  assert.ok(h.s.raf !== 0, "шум не должен отменять пружину");
  h.advance(1200);
  assert.ok(Math.abs(h.s.pos - (3600 - 688)) < 1);
  h.s.destroy();
});

check("асинхронный рост контента (картинка) держит якорь и низ без пружины", () => {
  const h = fixture({ stick: true });
  h.s.scrollTo(h.s.max, false);
  h.view.scrollHeight = 3600; // никто не звал preserve — RO увидит первым
  h.s.ro.cb();
  assert.equal(h.s.pos, 3600 - 688, "низ держится");
  assert.equal(h.s.raf, 0, "тихо, без анимации");
  h.s.destroy();
});

console.log(failures ? `FAILURES: ${failures}/${checks}` : `ALL ${checks} OK`);
process.exit(failures ? 1 : 0);
