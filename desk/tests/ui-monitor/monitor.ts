// Offline browser regression checks. Build with Vite from this directory.
import { buildRooms } from "../../app/src/rooms";
import { S, type Run } from "../../app/src/state";
import { bindRuns, runRowHTML } from "../../app/src/runlist";
import { mountUsage, usageShell } from "../../ui-kit/usage";
import "../../ui-kit/fonts.css";
import "../../ui-kit/tokens.css";
import "../../app/src/styles/app.css";

const style = document.createElement("style");
style.textContent = "body{overflow:auto}main{max-width:800px;margin:28px auto;padding:0 18px}pre{white-space:pre-wrap;font:12px var(--mono)}h1{font:28px var(--serif)}";
document.head.append(style);
document.querySelector("#theme")!.addEventListener("click", () => { document.documentElement.dataset.theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; });
const checks: string[] = [];
function check(ok: unknown, name: string) { if (!ok) throw new Error(name); checks.push(name); }
const wait = window.setTimeout.bind(window);
const settle = () => new Promise<void>(resolve => wait(resolve, 0));
const scheduled: Array<() => Promise<void>> = [];
window.setTimeout = ((fn: TimerHandler, ms?: number, ...args: unknown[]) => ms === 15000 ? (scheduled.push(fn as () => Promise<void>), 0) : wait(fn, ms, ...args)) as typeof window.setTimeout;
window.fetch = async () => { throw new Error("Unexpected network request in offline fixture"); };

try {
  localStorage.removeItem("frame.rooms.stub");
  const key = "window-" + crypto.randomUUID().replace(/-/g, "").slice(0, 8);
  const run: Run = { id: crypto.randomUUID(), kind: "chat_turn", status: "completed", chat_id: key, goal_head: "Проверка результата без потери фокуса" };
  check(!buildRooms([run], []).some(r => r.key === key), "An archived window is not restored from historical runs");
  check(buildRooms([run]).some(r => r.key === key), "Unavailable catalog preserves historical fallback");
  check(buildRooms([run], [{ peer_id: key, title: "Рабочий чат", kind: "window" }]).some(r => r.name === "Рабочий чат"), "Existing named room is preserved");
  check(buildRooms([{ ...run, chat_id: "-123", chat_title: "Telegram history" }], []).some(r => r.key === "-123"), "Telegram history remains available");
  const box = document.querySelector<HTMLElement>("#monitor")!;
  S.evCache.set(run.id, { id: run.id, manifest: { status: "completed" }, iterations: [] });
  box.innerHTML = runRowHTML(run);
  let redraws = 0;
  bindRuns(box, () => { redraws++; }, () => undefined);
  const head = box.querySelector<HTMLButtonElement>(".ev-head")!;
  head.focus(); head.click(); await settle();
  check(head.getAttribute("aria-expanded") === "true" && !box.querySelector<HTMLElement>(".ev-steps")!.hidden, "Activity details open in place");
  check(document.activeElement === head && !redraws, "Activity expansion preserves focus without page redraw");
  head.click(); check(head.getAttribute("aria-expanded") === "false", "Activity details collapse in place");

  const counts = { calls: 18, total_tokens: 42000, input_tokens: 40000, output_tokens: 2000, cache_read_tokens: 30000, cache_percent: 75, legacy_semantics: false };
  let usage = { status: "ok", today_total: counts, week_total: counts, days: [], models: [{ ...counts, name: "Example model" }], roles: [] };
  const providers = [{ name: "Example", status: "ok", observed_at: 1788780000, windows: [{ label: "Неделя", used_percent: 93, remaining_percent: 7, resets_at: null }, { label: "Неизвестное окно", used_percent: null, remaining_percent: null, resets_at: null }] }];
  const api = async <T>(path: string) => (path === "/api/usage" ? usage : { providers }) as T;
  box.innerHTML = usageShell(true);
  mountUsage(box, api); await settle();
  const panel = box.querySelector<HTMLDetailsElement>(".usage-panel")!;
  check(!panel.open, "Usage starts compact");
  check(panel.querySelectorAll(".usage-meter").length === 1, "Unknown limit is not represented as an empty meter");
  check(panel.querySelector(".usage-meter")!.getAttribute("aria-valuenow") === "7" && panel.querySelector<HTMLElement>(".usage-meter i")!.style.width === "7%", "Meter and label both show remaining quota");
  check(panel.querySelector(".usage-meter i")!.classList.contains("usage-low"), "Low remaining quota is highlighted");
  panel.open = true; await settle();
  const model = panel.querySelector<HTMLDetailsElement>(".usage-details")!;
  model.open = true; model.querySelector<HTMLElement>("summary")!.focus(); await settle();
  await scheduled.shift()!();
  check(panel.open && panel.querySelector<HTMLDetailsElement>(".usage-details")!.open, "Polling preserves both disclosures");
  check(document.activeElement === panel.querySelector(".usage-details > summary"), "Polling preserves keyboard focus");
  box.innerHTML = usageShell(true); mountUsage(box, api); await settle();
  check(box.querySelector<HTMLDetailsElement>(".usage-panel")!.open, "Usage disclosure survives screen remount");
  usage = { ...usage, status: "unavailable" };
  await scheduled.pop()!();
  check(box.querySelector("[data-usage-brief]")!.textContent!.includes("недоступен"), "Unavailable usage is explicit in the compact summary");
  document.body.dataset.testStatus = "passed";
  document.querySelector("#results")!.textContent = JSON.stringify({ passed: checks.length, checks }, null, 2);
} catch (e) {
  document.body.dataset.testStatus = "failed";
  document.querySelector("#results")!.textContent = String(e) + "\n" + checks.join("\n");
  throw e;
}
