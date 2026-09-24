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
import { button, card, field, receipt } from "../../ui-kit/dom";

interface AgentConfigState {
  ok: boolean;
  why: string;
  path: string;
  config: Record<string, any>;
  editable: string[];
  mtime_ns: string;
  restart_needed?: boolean;
}

/** Карточка настроек серверного агента. Пустая, если канал их не отдаёт. */
export function serverAgentCard(): { el: HTMLElement } {
  const box = el("div");
  const status = el("p", "field-hint", "спрашиваю канал…");
  const fields = el("div");
  const actions = el("div", "actions");
  const note = el("span", "receipt");
  const save = button("Сохранить на сервере", "primary", () => void store());
  actions.append(save, note);
  box.append(status, fields, actions);

  let seen = "";
  let draft: Record<string, any> = {};

  const put = (block: string, key: string, value: string | number) => {
    const was = draft[block] && typeof draft[block] === "object" ? draft[block] : {};
    draft[block] = { ...was, [key]: value };
  };

  const draw = (state: AgentConfigState) => {
    fields.replaceChildren();
    if (!state.ok) {
      status.textContent = state.why;
      save.disabled = true;
      return;
    }
    seen = state.mtime_ns;
    draft = {};
    const cfg = state.config || {};
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
    save.disabled = false;
  };

  const store = async () => {
    if (!Object.keys(draft).length) {
      receipt(note, "нечего сохранять — ничего не менялось");
      return;
    }
    save.disabled = true;
    receipt(note, "пишу на сервере…");
    try {
      const got = await post<{ saved: string[]; dropped: string[]; note: string; mtime_ns: string }>(
        "/api/agent-config", { config: draft, mtime_ns: seen });
      seen = got.mtime_ns;
      draft = {};
      const dropped = got.dropped?.length ? ` Отброшено (не правится отсюда): ${got.dropped.join(", ")}.` : "";
      receipt(note,
        `Сохранено: ${got.saved.join(", ")}.${dropped} ${got.note}`, "ok");
    } catch (e) {
      const said = humanError(e);
      // 409 — не поломка, а ответ: конфиг на сервере изменился под нами (агент
      // правит его сам рукой switch_brain).
      receipt(note, said.text, "err");
      void load();
    } finally {
      save.disabled = false;
    }
  };

  const load = async () => {
    try {
      draw(await api<AgentConfigState>("/api/agent-config"));
    } catch (e) {
      status.textContent = "канал не ответил про настройки агента: " + humanError(e).text;
      save.disabled = true;
    }
  };
  void load();

  return {
    el: card("Агент на сервере", box,
      "Мозг, Telegram и имена агента правятся здесь и записываются в его файл на сервере. " +
      "Дерево читает файл на старте — после сохранения перезапусти агента на экране «Система»."),
  };
}
