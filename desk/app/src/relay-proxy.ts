import { shell } from "../../ui-kit/window/api";
import { button, el, field } from "../../ui-kit/dom";
import { humanError } from "../../ui-kit/window/lib";

type ProxySettings = { mode: string; protocol: string; host: string; port: string; username: string; password: string; [key: string]: unknown };
export function relayProxy(saved: unknown, options: { application?: boolean } = {}) {
  const prior = saved && typeof saved === "object" ? saved as Record<string, unknown> : {};
  const value: ProxySettings = { ...prior, mode: String(prior.mode || "system"), protocol: String(prior.protocol || "http"),
    host: String(prior.host || ""), port: String(prior.port || ""), username: String(prior.username || ""), password: String(prior.password || "") };
  const element = el("section", ""); element.style.marginTop = "24px";
  if (!options.application) element.append(el("h4", "", "Подключение реле"));
  const select = (label: string, selected: string, choices: [string, string][], changed: (v: string) => void) => {
    const wrapper = el("label", "field"); const input = el("select", "field-input");
    if (!choices.some(([key]) => key === selected)) choices = [...choices, [selected, `Неизвестное значение: ${selected}`]];
    for (const [key, title] of choices) { const option = el("option", "", title); option.value = key; input.append(option); }
    input.value = selected; input.addEventListener("change", () => changed(input.value));
    wrapper.append(el("span", "", label), input); return wrapper;
  };
  const detail = el("div", "form-grid two"); detail.style.marginTop = "12px";
  const show = () => { detail.hidden = value.mode !== "proxy"; detail.style.display = detail.hidden ? "none" : "grid"; };
  element.append(select("Маршрут", value.mode, [["system", "Системные настройки"], ["direct", "Напрямую, без прокси"], ["proxy", "Через выбранный прокси"]], mode => { value.mode = mode; show(); }));
  detail.append(select("Протокол", value.protocol, options.application ? [["http", "HTTP"], ["https", "HTTPS"]] : [["http", "HTTP"], ["https", "HTTPS"], ["socks5h", "SOCKS5 · DNS через прокси"], ["socks5", "SOCKS5 · локальный DNS"]], v => value.protocol = v),
    field("Сервер", value.host, v => value.host = v, { placeholder: "proxy.example.org", mono: true }),
    field("Порт", value.port, v => value.port = v, { type: "number", placeholder: "1080", mono: true }),
    field("Имя пользователя · необязательно", value.username, v => value.username = v),
    field("Пароль", value.password, v => value.password = v, { type: "password" }));
  const result = el("p", "receipt"); result.setAttribute("aria-live", "polite");
  const check = button("Проверить сохранённое подключение", "quiet", async () => {
    check.disabled = true; result.textContent = "Проверяю выбранный маршрут, без генерации…";
    try {
      const response = await shell<{ ok: boolean; http_status?: number; generation_sent?: boolean; error?: string }>("relay_network_check");
      result.textContent = response.ok ? "Сохранённый маршрут отвечает; запрос модели не отправлялся. Это не проверка оставшейся квоты."
        : response.http_status ? `Сервер ответил HTTP ${response.http_status}; доступ не подтверждён, запрос модели не отправлялся.` : response.error || "Подключение не подтверждено";
    } catch (error) { result.textContent = humanError(error).text; }
    finally { check.disabled = false; }
  });
  element.append(detail, el("p", "field-hint", options.application
    ? "Один маршрут для Telegram-бота, его проверки, кнопки Mini App и автоматического HTTPS. Для локального серверного моста обычно указывают HTTP, 127.0.0.1 и порт 8888. Сохрани настройки и перезапусти движок; Mini App переподключится автоматически. Подключение реле настраивается отдельно."
    : "Сначала нажми «Сохранить»: новый маршрут применяется к реле, включая вход, обновление токенов и лимиты. Перезапустится только реле; текущий запрос модели может прерваться. Проверка использует сохранённые настройки, не черновик. Этот прокси не меняет подключение Telegram и других программ."));
  if (!options.application) element.append(check, result);
  show();
  return { element, collect: () => ({ ...value }), set: (next:Partial<ProxySettings>) => {
    Object.assign(value,next);
    const selects=element.querySelectorAll<HTMLSelectElement>('select');
    [value.mode,value.protocol].forEach((item,index)=>{selects[index].value=item;selects[index].dispatchEvent(new Event('change',{bubbles:true}))});
    const inputs=detail.querySelectorAll<HTMLInputElement>('input');
    [value.host,value.port,value.username,value.password].forEach((item,index)=>{inputs[index].value=item;inputs[index].dispatchEvent(new Event('input',{bubbles:true}))});
    show();
  }, error: () => {
    if (!["system", "direct", "proxy"].includes(value.mode)) return "Выбери маршрут подключения.";
    if (value.mode !== "proxy") return "";
    if (!(options.application ? ["http", "https"] : ["http", "https", "socks5", "socks5h"]).includes(value.protocol) || !value.host.trim() || /[\/@?#\\\s]/.test(value.host) || !/^\d+$/.test(value.port) || Number(value.port) < 1 || Number(value.port) > 65535)
      return "Для прокси нужны протокол, сервер без пути и порт 1–65535.";
    if (value.password && !value.username) return "Для пароля прокси укажи имя пользователя.";
    return "";
  } };
}
