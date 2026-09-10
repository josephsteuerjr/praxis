// Карточка «Агенты»: кто живёт в этой установке и как завести ещё одного.
//
// До 11.09 правило было «одна папка = один агент», и второго заводили второй
// установкой программы: свой рантайм, своё дерево кода, своё обновление — 600 МБ
// ради второго характера. Теперь агент — это папка `agents/<id>/` рядом с
// программой: свой дом, свой порт, свой бот и свой мозг, а рантайм, код агента
// и обновление общие.
//
// ⚠ Карточка НИЧЕГО не решает сама: список и заведение — у оболочки
// (`agents_list`, `agent_add`), а правила (id из имени, свободный порт, что
// наследуется от корневого) — в питоне (`localharness/agents.py`). Здесь только
// экран: две реализации «завести агента» разъехались бы на первом же имени.
import { shell } from "../../ui-kit/window/api";
import { el, esc, humanError, toast } from "../../ui-kit/window/lib";
import { button, card, field } from "../../ui-kit/dom";

interface AgentRow {
  id: string;
  name: string;
  tree: string;
  port: number;
  enabled: boolean;
  base: boolean;
  conflict: string;
  raised: boolean;
  current: boolean;
}

/**
 * Карточка живёт только в оболочке: в браузере и на телефоне переключать и
 * заводить нечего — они пришли к одному каналу по адресу.
 */
export function agentsCard(): { el: HTMLElement } {
  const box = el("div", "agents-card");
  const list = el("div", "agents-list");
  const add = el("div", "actions");
  const nameBox = field("Имя нового агента", "", (v) => (wanted = v), {
    placeholder: "Мира",
    hint: "Имя войдёт в его подписи и в название папки. Мозг и ограду он унаследует от этого агента, бот и тело — нет: они у каждого свои.",
  });
  let wanted = "";
  let busy = false;

  const made = el("div", "hint");
  const addBtn = button("Завести агента", "primary", () => {
    if (busy) return;
    const named = wanted.trim();
    if (!named) {
      toast("У агента должно быть имя — им он подписывает свои слова.");
      return;
    }
    busy = true;
    addBtn.disabled = true;
    void shell<{ id: string; name: string; port: number; dir: string }>("agent_add", { name: named })
      .then((got) => {
        made.textContent =
          `Агент «${got.name}» заведён: папка ${got.dir}, порт ${got.port}. ` +
          `Он поднимется после перезапуска программы — тогда же появится в переключателе на полке. ` +
          `Мозг у него уже есть, а бота и владельца впиши в его собственных настройках.`;
        made.classList.add("ok");
        void paint();
      })
      .catch((e) => toast(humanError(e).text))
      .finally(() => {
        busy = false;
        addBtn.disabled = false;
      });
  });
  add.append(addBtn);

  async function paint(): Promise<void> {
    try {
      const got = await shell<{ agents: AgentRow[]; current: string }>("agents_list");
      list.innerHTML = "";
      for (const a of got.agents) {
        const row = el("div", "agent-line");
        // Состояние словами, а не значком: «поднят» здесь означает живого
        // ребёнка этой оболочки, а не строчку в конфиге.
        const state = a.conflict
          ? `спорит за порт ${a.port} с «${a.conflict}» — не поднимаю`
          : !a.enabled
            ? "снят в его настройках"
            : a.raised
              ? `поднят, порт ${a.port}`
              : `порт ${a.port} — поднимется после перезапуска`;
        row.innerHTML =
          `<span class="agent-line-name">${esc(a.name)}${a.current ? " · сейчас в окне" : ""}</span>` +
          `<span class="agent-line-state">${esc(state)}</span>` +
          `<span class="mono agent-line-home">${esc(a.tree)}</span>`;
        if (!a.current && !a.conflict) {
          row.append(
            button("Открыть", "quiet", () => {
              void shell("switch_agent", { id: a.id }).catch((e) => toast(humanError(e).text));
            }),
          );
        }
        list.append(row);
      }
    } catch (e) {
      list.textContent = humanError(e).text;
    }
  }
  void paint();

  box.append(list, nameBox, add, made);
  return {
    el: card(
      "Агенты",
      box,
      "Одна установка — сколько угодно агентов: рантайм, код и обновление общие, дом и настройки у каждого свои. " +
        "Переключает окно полка слева и значок у часов; ярлык на конкретного агента — helene.exe --agent <папка>.",
    ),
  };
}
