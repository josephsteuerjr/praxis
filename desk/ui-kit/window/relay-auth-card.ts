import { api, post, shell } from "./api";
import { el, humanError } from "./lib";
import { button, card } from "../dom";
import { relayAccounts } from '../../app/src/relay-accounts';
import { subscriptionTransport } from '../relay-subscription';

export function relayAuthCard(base: string): HTMLElement {
  const body = el("div");
  const out = el("p", "receipt");
  let host = base;
  try { host = new URL(base).host; } catch { /* native validates destination */ }
  body.append(el("p", "field-hint", `Вход на сервере ${host}: запроси код и подтверди его на телефоне или ПК. Сервер сам получит и сохранит вход; браузер и SSH-туннель на сервере не нужны.`));
  const accounts = relayAccounts(subscriptionTransport(<T>(path: string, payload?: unknown) => payload === undefined ? api<T>(path) : post<T>(path, payload)));
  body.append(accounts.element);
  const transfer = el('details'); transfer.append(el('summary', '', 'Передать уже сохранённый вход с этого компьютера'));
  transfer.append(el('p', 'field-hint', 'Сервер получит доступ к подписке из слота 1 этого приложения. Передача идёт по защищённому подключению.'));
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
      await accounts.refresh();
    } catch (e) {
      out.textContent = humanError(e).text;
    } finally { start.disabled = replace.disabled = false; }
  };
  const start = button("Передать вход ChatGPT на сервер", "quiet", () => void send(false));
  transfer.append(start, replace, out); body.append(transfer);
  body.append(el("p", "field-hint", "После входа SSH-туннель не нужен. Для обновлений контейнера сохрани папку данных сервера в постоянном томе. Доктор покажет результат приёма и состояние хранения входа."));
  return card("ChatGPT на сервере", body);
}
