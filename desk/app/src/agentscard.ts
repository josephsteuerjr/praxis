// Карточка «Агенты»: кто живёт в этой установке и как завести ещё одного.
//
// До 11.09 правило было «одна папка = один агент», и второго заводили второй
// установкой программы: свой рантайм, своё дерево кода, своё обновление — 600 МБ
// ради второго характера. Теперь агент — это папка `agents/<id>/` рядом с
// программой: свой дом, свой порт, свой бот и свой мозг, а рантайм, код агента
// и обновление общие.
//
// ⚠ Карточка НИЧЕГО не решает сама: список, заведение, дефолт, гашение и
// удаление — у оболочки (`agents_list`, `agent_add`, `agent_default_set`,
// `agent_enabled_set`, `agent_remove`), а правила (id из имени, свободный
// порт, что наследуется от корневого) — в питоне (`localharness/agents.py`).
// Здесь только экран: две реализации «завести агента» разъехались бы на первом
// же имени.
//
// Конституция при заведении (1.4.0): канон — молча, как раньше; «унаследовать»
// и «свой текст» читают soul/SOUL.md ТЕКУЩЕГО агента тем же каналом, что и
// раздел «Файлы» (/api/md), — живой файл, а не копия, вшитая в окно.
import { api, shell } from "../../ui-kit/window/api";
import { el, esc, humanError, toast } from "../../ui-kit/window/lib";
import { button, card, field } from "../../ui-kit/dom";

/**
 * Слова отказа оболочки — владельцу, а не «Не получилось.».
 *
 * humanError сворачивает незнакомый текст в «Не получилось.» и прячет причину
 * в detail — годится для канала, но не для глаголов оболочки: их Err уже
 * написан по-русски для человека («агент погашен…», «порт спорит…»).
 * Показываем его как есть; пустой отказ — обычным humanError.
 */
const shellToast = (e: unknown) => {
  const msg = e instanceof Error ? e.message.trim() : "";
  toast(msg || humanError(e).text);
};

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
  /** Кого окно открывает при старте установки. Поля может не быть (старая
   *  оболочка) — тогда ни у кого нет пометки, и кнопка у всех просто кнопка. */
  default?: boolean;
}

type SoulKind = "canon" | "inherit" | "text";

/**
 * Карточка живёт только в оболочке: в браузере и на телефоне переключать и
 * заводить нечего — они пришли к одному каналу по адресу.
 */
export function agentsCard(mac = false): { el: HTMLElement } {
  const box = el("div", "agents-card");
  const list = el("div", "agents-list");
  const add = el("div", "actions");
  const nameBox = field("Имя нового агента", "", (v) => (wanted = v), {
    placeholder: "Мира",
    hint: "Имя войдёт в его подписи и в название папки. Мозг и ограду он унаследует от этого агента, бот и тело — нет: они у каждого свои.",
  });
  let wanted = "";
  let busy = false;
  let currentName = ""; // чьё окно открыто — этим именем подписана опция наследования

  // --- конституция нового агента: канон / унаследовать / свой текст ----------
  //
  // До 1.4.0 второй агент получал канон молча, и «нельзя выбрать конституцию»
  // было правдой. Канон остаётся умолчанием без единого движения; два других
  // режима раскрывают редактор, но текст приезжает ИЗ ФАЙЛА текущего агента,
  // а не из памяти окна: конституция — живой документ, окно его не хранит.
  let soulKind: SoulKind = "canon";
  let soulTouched = false; // владелец правил текст — смена режима его не затирает
  const soulPick = el("div", "choice");
  soulPick.setAttribute("role", "radiogroup");
  const soulBtns: Array<{ b: HTMLButtonElement; value: SoulKind; title: HTMLElement }> = [];
  const soulArea = el("textarea", "md-editor");
  soulArea.style.minHeight = "300px";
  soulArea.spellcheck = false;
  soulArea.hidden = true;
  const soulWarn = el("p", "field-hint", "Имена в тексте — от агента-донора, поправь, кто чьё.");
  soulWarn.hidden = true;
  const soulLoad = el("span", "receipt");
  const syncSoul = () => {
    for (const { b, value } of soulBtns) b.setAttribute("aria-checked", String(value === soulKind));
    soulArea.hidden = soulKind === "canon";
    soulWarn.hidden = soulKind !== "inherit";
  };
  for (const [value, title, text] of [
    ["canon", () => "Каноническая Hélène", "как у всех: имена подставятся сами"],
    ["inherit", () => (currentName ? `Унаследовать от „${currentName}“` : "Унаследовать от текущего"), "конституция этого агента — стартовая точка"],
    ["text", () => "Свой текст", "начни с канона и перепиши под него"],
  ] as Array<[SoulKind, () => string, string]>) {
    const b = el("button", "choice-item");
    b.type = "button";
    b.setAttribute("role", "radio");
    b.dataset.value = value;
    const titleSpan = el("span", "choice-title", title());
    b.append(titleSpan, el("span", "choice-text", text));
    b.addEventListener("click", () => void pickSoul(value));
    soulBtns.push({ b, value, title: titleSpan });
    soulPick.append(b);
  }
  syncSoul();
  const pickSoul = async (value: SoulKind) => {
    soulKind = value;
    syncSoul();
    // Канон ничего не читает; правленый текст режим тоже не затирает — работа
    // владельца дороже свежей копии файла.
    if (value === "canon" || soulTouched) return;
    soulLoad.className = "receipt";
    soulLoad.textContent = "читаю конституцию…";
    try {
      const doc = await api<{ error?: string; text?: string }>("/api/md?path=" + encodeURIComponent("soul/SOUL.md"));
      if (doc.error) throw new Error(doc.error);
      if (!soulTouched && soulKind !== "canon") soulArea.value = doc.text || "";
      soulLoad.textContent = "";
    } catch (e) {
      soulLoad.className = "receipt err";
      soulLoad.textContent = "Не прочиталось: " + humanError(e).text + " — можно вписать текст руками.";
    }
  };
  soulArea.addEventListener("input", () => (soulTouched = true));
  const soulBox = el("div");
  soulBox.style.marginTop = "14px";
  soulBox.append(el("span", "models-label", "Конституция"), soulPick, soulArea, soulWarn, soulLoad);
  const resetSoul = () => {
    soulKind = "canon";
    soulTouched = false;
    soulArea.value = "";
    soulLoad.className = "receipt";
    soulLoad.textContent = "";
    syncSoul();
  };

  const made = el("div", "hint");
  const addBtn = button("Завести агента", "primary", () => {
    if (busy) return;
    const named = wanted.trim();
    if (!named) {
      toast("У агента должно быть имя — им он подписывает свои слова.");
      return;
    }
    if (soulKind !== "canon" && !soulArea.value.trim()) {
      toast(soulKind === "inherit"
        ? "Текст конституции не прочитался или пуст — выбери каноническую или вписать руками."
        : "Своя конституция пуста — впиши текст или выбери каноническую.");
      return;
    }
    busy = true;
    addBtn.disabled = true;
    const args: Record<string, unknown> = { name: named };
    // Канон — ключа soul нет вовсе: питон понимает это как «как раньше».
    if (soulKind !== "canon") args.soul = { kind: soulKind, text: soulArea.value };
    void shell<{ id: string; name: string; port: number; dir: string }>("agent_add", args)
      .then((got) => {
        made.textContent =
          `Агент «${got.name}» заведён: папка ${got.dir}, порт ${got.port}. ` +
          `Он поднимется после перезапуска программы — тогда же появится в переключателе на полке. ` +
          `Мозг у него уже есть, а бота и владельца впиши в его собственных настройках.`;
        made.classList.add("ok");
        resetSoul();
        void paint();
      })
      .catch(shellToast)
      .finally(() => {
        busy = false;
        addBtn.disabled = false;
      });
  });
  add.append(addBtn);

  async function paint(): Promise<void> {
    try {
      const got = await shell<{ agents: AgentRow[]; current: string }>("agents_list");
      const cur = got.agents.find((a) => a.current);
      if (cur && cur.name !== currentName) {
        currentName = cur.name;
        for (const it of soulBtns) if (it.value === "inherit") it.title.textContent = `Унаследовать от „${currentName}“`;
      }
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
          `<span class="agent-line-name">${esc(a.name)}${a.current ? " · сейчас в окне" : ""}${a.default ? " · открывается по умолчанию" : ""}</span>` +
          `<span class="agent-line-state">${esc(state)}</span>` +
          `<span class="mono agent-line-home">${esc(a.tree)}</span>`;
        const acts = el("div", "actions");
        acts.style.gridColumn = "1/-1";
        acts.style.marginBottom = "0";
        if (!a.current && !a.conflict) {
          // Снятый агент открывается той же кнопкой: «снять» — не «удалить», а
          // отказ «он погашен» скажет оболочка словами — окно покажет их тостом.
          acts.append(
            button("Открыть", "quiet", () => {
              void shell("switch_agent", { id: a.id }).catch(shellToast);
            }),
          );
        }
        const defBtn = button(a.default ? "Открывается по умолчанию" : "Открывать по умолчанию", "quiet", () => {
          if (a.default) return;
          void shell("agent_default_set", { id: a.id })
            .then(() => void paint())
            .catch(shellToast);
        });
        defBtn.setAttribute("aria-pressed", String(!!a.default));
        if (a.default) defBtn.disabled = true;
        acts.append(
          defBtn,
          button(a.enabled ? "Погасить" : "Поднять", "quiet", () => {
            void shell("agent_enabled_set", { id: a.id, enabled: !a.enabled })
              .then(() => void paint())
              .catch(shellToast);
          }),
        );
        if (!a.current && !a.base) {
          // Удаление — двойное подтверждение: сначала диалог, в котором id
          // агента нужно ВПИСАТЬ рукой. Один «Ок» на необратимое — не рубеж.
          const kill = el("div");
          kill.hidden = true;
          kill.style.gridColumn = "1/-1";
          let typed = "";
          const killField = field(`id агента: ${a.id}`, "", (v) => (typed = v), { mono: true });
          const killRow = el("div", "actions");
          killRow.style.marginTop = "8px";
          killRow.append(
            button("Удалить навсегда", "danger", () => {
              if (typed.trim().toLowerCase() !== a.id) {
                toast(`Не совпало: введи id «${a.id}» точно — удаление неотвратимо.`);
                return;
              }
              // Путь чердака — из ОТВЕТА оболочки (фикс-волна 06.10, F5):
              // питон возвращает dest, и Rust передаёт его дальше. Тексты на
              // экране про «_state/attic» врали: настоящий чердак — папка
              // agents-attic рядом с agents/ у корня установки.
              void shell<{ attic?: string }>("agent_remove", { id: a.id })
                .then((made) => {
                  const where = made && typeof made.attic === "string" && made.attic.trim()
                    ? `папка уехала в ${made.attic}`
                    : "папка уехала на чердак agents-attic рядом с папкой agents";
                  toast(`Агент «${a.name}» удалён: ${where}.`);
                  void paint();
                })
                .catch(shellToast);
            }),
            button("Отмена", "quiet", () => (kill.hidden = true)),
          );
          kill.append(
            el("p", "field-hint",
              `Удаление из окна неотвратимо: дом агента уезжает на чердак — папка agents-attic рядом с папкой agents у корня установки, — и вернуть его оттуда можно только руками. ` +
              `Если он сейчас поднят — сначала погаси его кнопкой выше.`),
            killField,
            killRow,
          );
          acts.append(button("Удалить…", "danger", () => (kill.hidden = false)));
          row.append(acts, kill);
        } else {
          row.append(acts);
        }
        list.append(row);
      }
    } catch (e) {
      list.textContent = humanError(e).text;
    }
  }
  void paint();

  box.append(list, nameBox, soulBox, add, made);
  return {
    el: card(
      "Агенты",
      box,
      "Одна установка — сколько угодно агентов: рантайм, код и обновление общие, дом и настройки у каждого свои. " +
        // Значок и путь бинаря — этой системы: на Mac оболочка живёт в бандле, а
        // значок — в строке меню.
        (mac
          ? "Переключает окно полка слева и значок в строке меню; открыть конкретного агента — Helene.app/Contents/MacOS/helene --agent <папка>."
          : "Переключает окно полка слева и значок у часов; ярлык на конкретного агента — helene.exe --agent <папка>."),
    ),
  };
}
