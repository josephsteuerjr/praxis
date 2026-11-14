import { t as uiText } from "../../lang/lang";
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
export function agentsCard(_mac = false, _linux = false): { el: HTMLElement } {
  const box = el("div", "agents-card");
  const list = el("div", "agents-list");
  const add = el("div", "actions");
  const nameBox = field(uiText("ui.app.src.agentscard.799f531262"), "", (v) => (wanted = v), {
    placeholder: uiText("ui.app.src.agentscard.de987a5f33"),
    hint: uiText("ui.app.src.agentscard.a8c3eee0e8"),
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
  const soulWarn = el("p", "field-hint", uiText("ui.app.src.agentscard.cba4faec1b"));
  soulWarn.hidden = true;
  const soulLoad = el("span", "receipt");
  const syncSoul = () => {
    for (const { b, value } of soulBtns) b.setAttribute("aria-checked", String(value === soulKind));
    soulArea.hidden = soulKind === "canon";
    soulWarn.hidden = soulKind !== "inherit";
  };
  for (const [value, title, text] of [
    ["canon", () => uiText("ui.app.src.agentscard.2ed1947e35"), uiText("ui.app.src.agentscard.5d5fe9b0b2")],
    ["inherit", () => (currentName ? uiText("ui.app.src.agentscard.d8b96c7703", {p0: String(currentName)}) : uiText("ui.app.src.agentscard.15c9f72893")), uiText("ui.app.src.agentscard.2f115e8d82")],
    ["text", () => uiText("ui.app.src.agentscard.cb161e488b"), uiText("ui.app.src.agentscard.98df5d3b6c")],
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
    soulLoad.textContent = uiText("ui.app.src.agentscard.26261767c3");
    try {
      const doc = await api<{ error?: string; text?: string }>("/api/md?path=" + encodeURIComponent("soul/SOUL.md"));
      if (doc.error) throw new Error(doc.error);
      if (!soulTouched && soulKind !== "canon") soulArea.value = doc.text || "";
      soulLoad.textContent = "";
    } catch (e) {
      soulLoad.className = "receipt err";
      soulLoad.textContent = uiText("ui.app.src.agentscard.f750546f2d") + humanError(e).text + uiText("ui.app.src.agentscard.fbd750b137");
    }
  };
  soulArea.addEventListener("input", () => (soulTouched = true));
  const soulBox = el("div");
  soulBox.style.marginTop = "14px";
  soulBox.append(el("span", "models-label", uiText("ui.app.src.agentscard.8a9346b6c0")), soulPick, soulArea, soulWarn, soulLoad);
  const resetSoul = () => {
    soulKind = "canon";
    soulTouched = false;
    soulArea.value = "";
    soulLoad.className = "receipt";
    soulLoad.textContent = "";
    syncSoul();
  };

  const made = el("div", "hint");
  const addBtn = button(uiText("ui.app.src.agentscard.8b4e33200e"), "primary", () => {
    if (busy) return;
    const named = wanted.trim();
    if (!named) {
      toast(uiText("ui.app.src.agentscard.ff1f5a0df2"));
      return;
    }
    if (soulKind !== "canon" && !soulArea.value.trim()) {
      toast(soulKind === "inherit"
        ? uiText("ui.app.src.agentscard.3cd10aa97d")
        : uiText("ui.app.src.agentscard.ecfb295da6"));
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
          uiText("ui.app.src.agentscard.767305285b", {p0: String(got.name), p1: String(got.dir), p2: String(got.port)}) +
          // 12.11: the supervisor re-reads the roster at once; no programme restart is needed.
          uiText("ui.app.src.agentscard.bcaad1a197") +
          uiText("ui.app.src.agentscard.fbb2521e39");
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

  // Доктор одним кликом (1.4.1). Канон души и знания о харнессе читает
  // программа из поставки (resources/souls/doctor.md, doctor-skills/) —
  // окно шлёт одно слово, никаких текстов через argv. Имя — «Доктор»: его
  // подписи и папка; занято — оболочка откажет словами, и это честнее
  // тихого «доктор-2» без спроса.
  const docBtn = button(uiText("ui.app.src.agentscard.6bccc9c8ba"), "quiet", () => {
    if (busy) return;
    busy = true;
    docBtn.disabled = true;
    void shell<{ id: string; name: string; port: number; dir: string }>("agent_add", {
      name: uiText("ui.app.src.agentscard.5d2a0ca52f"),
      soul: { kind: "doctor" },
    })
      .then((got) => {
        toast(uiText("ui.app.src.agentscard.69e56cb034", {p0: String(got.dir)}));
        void paint();
      })
      .catch(shellToast)
      .finally(() => {
        busy = false;
        docBtn.disabled = false;
      });
  });
  add.append(docBtn);

  async function paint(): Promise<void> {
    try {
      const got = await shell<{ agents: AgentRow[]; current: string }>("agents_list");
      const cur = got.agents.find((a) => a.current);
      if (cur && cur.name !== currentName) {
        currentName = cur.name;
        for (const it of soulBtns) if (it.value === "inherit") it.title.textContent = uiText("ui.app.src.agentscard.d8b96c7703", {p0: String(currentName)});
      }
      list.innerHTML = "";
      for (const a of got.agents) {
        const row = el("div", "agent-line");
        // Состояние словами, а не значком: «поднят» здесь означает живого
        // ребёнка этой оболочки, а не строчку в конфиге.
        const state = a.conflict
          ? uiText("ui.app.src.agentscard.3c3edca899", {p0: String(a.port), p1: String(a.conflict)})
          : !a.enabled
            ? uiText("ui.app.src.agentscard.d5c6e04f24")
            : a.raised
              ? uiText("ui.app.src.agentscard.c46c4046bb", {p0: String(a.port)})
              : uiText("ui.app.src.agentscard.fc25be0b86", {p0: String(a.port)});
        row.innerHTML =
          `<span class="agent-line-name">${esc(a.name)}${a.current ? uiText("ui.app.src.agentscard.3342b5fbdf") : ""}${a.default ? uiText("ui.app.src.agentscard.44e83973dd") : ""}</span>` +
          `<span class="agent-line-state">${esc(state)}</span>` +
          `<span class="mono agent-line-home">${esc(a.tree)}</span>`;
        const acts = el("div", "actions");
        acts.style.gridColumn = "1/-1";
        acts.style.marginBottom = "0";
        if (!a.current && !a.conflict) {
          // Снятый агент открывается той же кнопкой: «снять» — не «удалить», а
          // отказ «он погашен» скажет оболочка словами — окно покажет их тостом.
          acts.append(
            button(uiText("ui.app.src.agentscard.ea1f408246"), "quiet", () => {
              void shell("switch_agent", { id: a.id }).catch(shellToast);
            }),
          );
        }
        const defBtn = button(a.default ? uiText("ui.app.src.agentscard.3ea9532599") : uiText("ui.app.src.agentscard.4a61535915"), "quiet", () => {
          if (a.default) return;
          void shell("agent_default_set", { id: a.id })
            .then(() => void paint())
            .catch(shellToast);
        });
        defBtn.setAttribute("aria-pressed", String(!!a.default));
        if (a.default) defBtn.disabled = true;
        acts.append(
          defBtn,
          button(a.enabled ? uiText("ui.app.src.agentscard.7998606a95") : uiText("ui.app.src.agentscard.3fc4751f57"), "quiet", () => {
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
          const killField = field(uiText("ui.app.src.agentscard.4078205525", {p0: String(a.id)}), "", (v) => (typed = v), { mono: true });
          const killRow = el("div", "actions");
          killRow.style.marginTop = "8px";
          killRow.append(
            button(uiText("ui.app.src.agentscard.abac4685c6"), "danger", () => {
              if (typed.trim().toLowerCase() !== a.id) {
                toast(uiText("ui.app.src.agentscard.ebdb71c214", {p0: String(a.id)}));
                return;
              }
              // Путь чердака — из ОТВЕТА оболочки (фикс-волна 06.10, F5):
              // питон возвращает dest, и Rust передаёт его дальше. Тексты на
              // экране про «_state/attic» врали: настоящий чердак — папка
              // agents-attic рядом с agents/ у корня установки.
              void shell<{ attic?: string }>("agent_remove", { id: a.id })
                .then((made) => {
                  const where = made && typeof made.attic === "string" && made.attic.trim()
                    ? uiText("ui.app.src.agentscard.60f9499cea", {p0: String(made.attic)})
                    : uiText("ui.app.src.agentscard.e86533f422");
                  toast(uiText("ui.app.src.agentscard.b358bfac6f", {p0: String(a.name), p1: String(where)}));
                  void paint();
                })
                .catch((error) => {
                  // A post-archive cleanup failure is reported as an error, yet
                  // the agent folder may already be in the attic. Re-read the
                  // roster instead of leaving a vanished agent's card actionable.
                  shellToast(error);
                  void paint();
                });
            }),
            button(uiText("ui.app.src.agentscard.8fbe9b75cb"), "quiet", () => (kill.hidden = true)),
          );
          kill.append(
            el("p", "field-hint",
              uiText("ui.app.src.agentscard.9b1010e57f") +
              uiText("ui.app.src.agentscard.0610e6bc2f")),
            killField,
            killRow,
          );
          acts.append(button(uiText("ui.app.src.agentscard.94a38117f5"), "danger", () => (kill.hidden = false)));
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
      uiText("ui.app.src.agentscard.96b789f637"),
      box,
      uiText("ui.app.src.agentscard.d31daf2180"),
    ),
  };
}
