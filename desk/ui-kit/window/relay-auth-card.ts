import { t as uiText } from "../../lang/lang";
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
  body.append(el("p", "field-hint", uiText("ui.ui.kit.window.relay.auth.card.4974f159fa", {p0: String(host)})));
  const accounts = relayAccounts(subscriptionTransport(<T>(path: string, payload?: unknown) => payload === undefined ? api<T>(path) : post<T>(path, payload)));
  body.append(accounts.element);
  const transfer = el('details'); transfer.append(el('summary', '', uiText("ui.ui.kit.window.relay.auth.card.d39291e3c2")));
  transfer.append(el('p', 'field-hint', uiText("ui.ui.kit.window.relay.auth.card.6e4ad76d56")));
  const replace = button(uiText("ui.ui.kit.window.relay.auth.card.fb3ffce378"), "quiet", () => void send(true));
  replace.hidden = true;
  const send = async (overwrite: boolean) => {
    start.disabled = replace.disabled = true;
    out.textContent = uiText("ui.ui.kit.window.relay.auth.card.78a43bae8d");
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
  const start = button(uiText("ui.ui.kit.window.relay.auth.card.37fd287f32"), "quiet", () => void send(false));
  transfer.append(start, replace, out); body.append(transfer);
  body.append(el("p", "field-hint", uiText("ui.ui.kit.window.relay.auth.card.e1052df0a1")));
  return card(uiText("ui.ui.kit.window.relay.auth.card.6f22f52402"), body);
}
