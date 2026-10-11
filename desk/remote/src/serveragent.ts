// Настройки агента, который живёт НА СЕРВЕРЕ, — прямо из окна.
//
// Что это и чем отличается от карточек Элен. У Элен агент рядом: её экран
// правит `helene.json` на этой же машине через оболочку (`config_save`). Здесь
// агента рядом нет — есть канал к нему, и правится ЕГО конфиг на сервере
// (`/api/agent-config`). Поэтому это не «те же карточки, но с флажком remote»,
// из-за которого 09.09 владелец увидел экран пустых полей, а другая работа:
// другой файл, другая дорога, другая кнопка сохранения.
//
// ⚠ И другая ГРАНИЦА. Правится только то, что про самого агента: мозг,
// Telegram, имена. Раскладку установки — где python, где код, порт канала,
// служба, реле — канал сюда не отдаёт и записать не даст (`deskd/agentcfg.py`,
// список `EDITABLE`). Ключ окна и так открывает многое, но подменить программу,
// которой агент запускается, он не должен.
//
// ⚠ Своя кнопка «Сохранить», а не общая с каркасом: общая пишет ЭТОТ конфиг
// (адрес сервера, ключ канала, имена окна), а здесь — конфиг на той стороне.
// Одна кнопка на два разных файла молча путала бы, что куда уехало.
import { api, post } from "../../ui-kit/window/api";
import { el, humanError } from "../../ui-kit/window/lib";
import { button, card, chips, field, receipt, toggle } from "../../ui-kit/dom";

interface AgentConfigState {
  ok: boolean;
  why: string;
  path: string;
  config: Record<string, any>;
  editable: string[];
  mtime_ns: string;
  restart_needed?: boolean;
  access?: { agent_mode: string; sandbox_enabled: boolean; session0: boolean; scope: string; socket_present: boolean } | null;
}

/** Карточка настроек серверного агента. Пустая, если канал их не отдаёт. */
export function serverAgentCard(): { el: HTMLElement } {
  const box = el("div");
  const status = el("p", "field-hint", "спрашиваю канал…");
  const fields = el("div");
  const actions = el("div", "actions");
  const note = el("span", "receipt");
  const save = button("Сохранить на сервере", "primary", () => void store());
  const reload = button("Перечитать настройки", "quiet", () => void load());
  save.disabled = true;
  actions.append(save, reload, note);
  box.append(status, fields, actions);

  let seen = "";
  let draft: Record<string, any> = {};
  let loaded: Record<string, any> = {};
  let revision = 0;
  let busy = false;
  let available = false;

  const put = (block: string, key: string, value: string | number | boolean) => {
    const original = loaded[block];
    const was = draft[block] || (original && typeof original === "object" && !Array.isArray(original) ? original : {});
    draft[block] = { ...was, [key]: value };
    revision++;
  };

  const draw = (state: AgentConfigState) => {
    fields.replaceChildren();
    available = state.ok;
    if (!state.ok) {
      status.textContent = state.why;
      save.disabled = true;
      return;
    }
    seen = state.mtime_ns;
    draft = {};
    const cfg = state.config || {};
    loaded = cfg;
    const model = cfg.model || {};
    const tg = cfg.telegram || {};
    const agent = cfg.agent || {};
    const owner = cfg.owner || {};
    status.textContent =
      `Правится файл агента на сервере: ${state.path}. Раскладка установки ` +
      `(где код, порт канала, служба, реле) отсюда не правится — она кладётся там, где агента разворачивали.`;
    fields.append(
      field("Адрес мозга", String(model.base_url || ""), (v) => put("model", "base_url", v.trim()),
            { mono: true, placeholder: "https://api.openai.com/v1" }),
      field("Модель", String(model.model || ""), (v) => put("model", "model", v.trim()),
            { mono: true }),
      field("Ключ мозга", String(model.key || ""), (v) => put("model", "key", v.trim()),
            { mono: true, hint: "Уезжает на сервер по тому же каналу, которым идёт переписка." }),
      field("Токен бота Telegram", String(tg.bot_token || ""), (v) => put("telegram", "bot_token", v.trim()),
            { mono: true }),
      field("Твой Telegram-id", String(tg.owner_id || ""), (v) => put("telegram", "owner_id", Number(v.trim()) || 0),
            { mono: true, hint: "Только цифры. Ноль — гейт агента не пустит никого, и бот будет молчать на всё." }),
      field("Имя агента", String(agent.name || ""), (v) => put("agent", "name", v.trim())),
      field("Имя владельца", String(owner.name || ""), (v) => put("owner", "name", v.trim())),
    );
    if (state.access) {
      const access = state.access;
      const mode = el("div");
      chips(mode, [{ value: "sandbox", label: "Песочница" }, { value: "interactive", label: "Интерактивный" }],
        access.agent_mode, (value) => put("access", "agent_mode", value), "Режим рук серверного агента");
      fields.append(mode, el("p", "field-hint",
        "Интерактивный режим открывает обычные руки в контейнере. Настройка применяется после перезапуска агента."));
      const root = toggle("Использовать выданный владельцем root-доступ к VPS", access.session0,
        (value) => put("access", "session0", value));
      fields.append(root, el("p", "field-hint", access.socket_present
        ? "Сокет сервера подключён. Действующее разрешение и исполнение команд проверяются брокером."
        : "Владелец ещё не подключил root-брокер на сервере. Этот выбор сохраняет запрос доступа; разрешение выдаётся на VPS."));
    }
    save.disabled = false;
  };

  const store = async () => {
    if (busy || !available) return;
    if (!Object.keys(draft).length) {
      receipt(note, "нечего сохранять — ничего не менялось");
      return;
    }
    busy = true;
    save.disabled = reload.disabled = true;
    const submitted = structuredClone(draft);
    const submittedRevision = revision;
    receipt(note, "пишу на сервере…");
    try {
      const got = await post<{ saved: string[]; dropped: string[]; note: string; mtime_ns: string }>(
        "/api/agent-config", { config: submitted, mtime_ns: seen });
      seen = got.mtime_ns;
      loaded = { ...loaded, ...submitted };
      if (revision === submittedRevision) draft = {};
      const dropped = got.dropped?.length ? ` Отброшено (не правится отсюда): ${got.dropped.join(", ")}.` : "";
      receipt(note,
        `Сохранено: ${got.saved.join(", ")}.${dropped} ${got.note}` +
        (Object.keys(draft).length ? " Новые правки ещё не сохранены." : ""), "ok");
    } catch (e) {
      const said = humanError(e);
      // 409 — не поломка, а ответ: конфиг на сервере изменился под нами (агент
      // правит его сам рукой switch_brain).
      receipt(note, said.text, "err");
      // Keep the owner's draft after a transport failure or freshness conflict.
      // Rereading is explicit because it replaces the displayed edits.
    } finally {
      busy = false;
      save.disabled = !available;
      reload.disabled = false;
    }
  };

  const load = async () => {
    if (busy) return;
    busy = true;
    save.disabled = reload.disabled = true;
    try {
      draw(await api<AgentConfigState>("/api/agent-config"));
    } catch (e) {
      available = false;
      status.textContent = "канал не ответил про настройки агента: " + humanError(e).text;
    } finally {
      busy = false;
      save.disabled = !available;
      reload.disabled = false;
    }
  };
  void load();

  return {
    el: card("Агент на сервере", box,
      "Мозг, Telegram, имена и режим серверного агента записываются в его файл на сервере. " +
      "Дерево читает файл на старте — после сохранения перезапусти агента на экране «Система»."),
  };
}
