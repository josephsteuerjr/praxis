import { api, shell } from "./api";
import { el, humanError } from "./lib";
import { button, card } from "../dom";

export function relayAuthCard(base: string): HTMLElement {
  const body = el("div");
  const out = el("p", "receipt");
  let host = base;
  try { host = new URL(base).host; } catch { /* native validates destination */ }
  body.append(el("p", "field-hint", `Передать вход ChatGPT из этого приложения на ${host}. Сервер получит доступ к подписке. Сначала войди в ChatGPT на этом компьютере; передача идёт по защищённому подключению.`));
  const replace = button("Заменить существующий вход на сервере", "quiet", () => void send(true));
  replace.hidden = true;
  const send = async (overwrite: boolean) => {
    start.disabled = replace.disabled = true;
    out.textContent = "Передаю вход выбранному серверу…";
    try {
      let receipt = await shell<{ id: string; phase: string; note: string }>("relay_auth_transfer", { replace: overwrite });
      for (let n = 0; n < 120; n++) {
        out.textContent = receipt.note;
        if (receipt.phase !== "queued") break;
        await new Promise((resolve) => window.setTimeout(resolve, 3000));
        if (!out.isConnected) return;
        receipt = await api(`/api/relay/auth/status?id=${encodeURIComponent(receipt.id)}`);
      }
      out.textContent = receipt.note;
      replace.hidden = receipt.phase !== "conflict";
    } catch (e) {
      out.textContent = humanError(e).text;
    } finally { start.disabled = replace.disabled = false; }
  };
  const start = button("Передать вход ChatGPT на сервер", "quiet", () => void send(false));
  body.append(start, replace, out);
  body.append(el("p", "field-hint", "После входа SSH-туннель не нужен. Для обновлений контейнера сохрани папку данных сервера в постоянном томе. Доктор покажет результат приёма и состояние хранения входа."));
  return card("ChatGPT на сервере", body);
}
