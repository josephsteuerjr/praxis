import { electron, shell } from './window/api';
import type { AccountView, RelayLogin } from '../app/src/relay-accounts';

export type SubscriptionState = { available?: boolean; installed?: boolean; component_ready?: boolean; note?: string;
  browser_login?: boolean; port?: number; catalog_revision?: string; slots?: Array<{slot: 'primary'|'secondary'; stored: boolean; active: boolean}>; login?: RelayLogin };
type Request = <T>(path: string, body?: unknown) => Promise<T>;

/** Uses the authenticated channel selected in this window, never local credentials. */
export function subscriptionTransport(request: Request, observed: (state: SubscriptionState) => void = () => {}) {
  let state: SubscriptionState = {};
  return async <T>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    if (command === 'relay_account') {
      state = await request<SubscriptionState>('/api/phone/subscription'); observed(state);
      const value: AccountView = { reachable: !!state.available, browser_login: state.browser_login, note: state.note,
        port: state.port, catalog_revision: state.catalog_revision, active_slot: state.slots?.find(row => row.active)?.slot, login: state.login,
        slots: (['primary', 'secondary'] as const).map(slot => {
          const row = state.slots?.find(row => row.slot === slot);
          return { slot, stored: !!row?.stored, loaded: !!row?.stored, serving: !!row?.active,
            pending: state.login?.pending && state.login.slot === slot,
            login_error: state.login?.slot === slot ? state.login.error : undefined };
        }) };
      return value as T;
    }
    if (command === 'open_login_page') {
      const login = state.login;
      if (!login?.pending || login.slot !== (args.slot || 'primary') || !login.url || login.expired
          || login.device_code && login.device_code.expires_at * 1000 <= Date.now()) throw Error('Страница этого входа уже недоступна');
      const url = new URL(login.url);
      if (url.protocol !== 'https:' || url.host !== 'auth.openai.com' || url.username || url.password || url.hash) throw Error('Неверный адрес входа');
      if (electron) await shell('open_path', { path: url.href });
      else window.open(url.href, '_blank', 'noopener,noreferrer');
      return undefined as T;
    }
    const action = ({relay_login:'login', relay_logout:'logout', relay_switch:'switch', relay_cancel_login:'cancel_login'} as Record<string,string>)[command];
    if (!action) throw Error('Недоступное действие подписки');
    return await request('/api/phone/subscription', {action, slot: args.slot || 'primary', method: args.method, login_id: args.login_id}) as T;
  };
}
