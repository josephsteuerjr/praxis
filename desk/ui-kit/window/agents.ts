// Переключатель агентов на полке. Появляется только там, где агентов больше
// одного, — и только в оболочке: в браузере и на телефоне переключать нечего,
// они приходят к одному каналу по адресу.
//
// ⚠ Переключение — это не «сменить вкладку»: окно строится заново с адресом и
// ключом другого агента (`switch_agent` в оболочке). Поэтому здесь нет ни
// перерисовки переписки, ни второго состояния — только просьба и отказ словами.
import { cfg, inTauri, shell } from "./api";
import { el, esc, toast } from "./lib";

/** Агент, как его называет оболочка в init-скрипте окна. */
export interface AgentRow {
  id: string;
  name: string;
  port: number;
  enabled: boolean;
  conflict: string;
  base: boolean;
}

export function roster(): AgentRow[] {
  const raw = (cfg as unknown as { agents?: AgentRow[] }).agents;
  return Array.isArray(raw) ? raw : [];
}

export function currentId(): string {
  return String((cfg as unknown as { agent_id?: string }).agent_id || "");
}

/** Попросить оболочку показать другого агента этой установки. */
export async function switchTo(id: string): Promise<void> {
  if (!inTauri) {
    toast("Переключение агентов есть только в приложении на этой машине.");
    return;
  }
  try {
    await shell("switch_agent", { id });
  } catch (e) {
    // Отказ оболочки — словами владельцу: «спорит за порт», «такого нет».
    toast(e instanceof Error ? e.message : String(e));
  }
}

/**
 * Нарисовать переключатель в начале полки.
 *
 * Ничего не рисует, когда агент один: строка «Hélène ▾», которая ни на что не
 * переключает, — это шум, а не выбор. Владелец, у которого агент один, не
 * должен даже знать, что здесь бывает список.
 */
export function mountSwitch(host: HTMLElement): void {
  const list = roster();
  if (list.length < 2 || !inTauri) return;
  const here = currentId();
  const mine = list.find((a) => a.id === here);
  const box = el("div", "agent-switch");
  const head = el("button", "agent-current");
  head.type = "button";
  head.setAttribute("aria-haspopup", "listbox");
  head.setAttribute("aria-expanded", "false");
  head.innerHTML =
    `<span class="agent-dot" aria-hidden="true"></span>` +
    `<span class="agent-name">${esc(mine?.name || "Агент")}</span>` +
    `<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6 8l4 4 4-4" /></svg>`;
  const menu = el("div", "agent-menu");
  menu.setAttribute("role", "listbox");
  menu.hidden = true;

  for (const a of list) {
    const row = el("button", "agent-row");
    row.type = "button";
    row.setAttribute("role", "option");
    row.setAttribute("aria-selected", String(a.id === here));
    const why = a.conflict
      ? `спорит за порт ${a.port} с «${a.conflict}»`
      : !a.enabled
        ? "снят в настройках"
        : `порт ${a.port}`;
    row.innerHTML =
      `<span class="agent-name">${esc(a.name)}</span><span class="agent-why">${esc(why)}</span>`;
    if (a.id === here) row.classList.add("is-current");
    // Спорящего за порт не открываем: окно ушло бы к каналу другого агента и
    // показало бы его переписку под чужим именем.
    if (a.conflict) row.disabled = true;
    row.addEventListener("click", () => {
      menu.hidden = true;
      head.setAttribute("aria-expanded", "false");
      if (a.id !== here) void switchTo(a.id);
    });
    menu.append(row);
  }

  head.addEventListener("click", () => {
    const open = menu.hidden;
    menu.hidden = !open;
    head.setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("click", (ev) => {
    if (!box.contains(ev.target as Node)) {
      menu.hidden = true;
      head.setAttribute("aria-expanded", "false");
    }
  });
  box.append(head, menu);
  host.prepend(box);
}
