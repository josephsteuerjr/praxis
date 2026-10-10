// Words follow the runtime policy; the archive count is a different quantity.
export interface FoldState {
  room: string; hot: number | null; keep: number; offer_at: number; hard_at: number;
  token_cap?: number; tape_chars?: number; offer_mode?: boolean; backstop_hours?: number;
  policy_source?: string;
  delivery?: { state: string; retry_at: number; retries: number; at: number } | null;
  offer: Record<string, unknown> | null;
  pending: { id?: string; at?: string } | null;
  receipt: { state?: string; note?: string; folded?: number; at?: string } | null;
}

export function deliveryWords(st: FoldState, now = Date.now()) {
  const d = st.delivery;
  if (!d || now / 1000 - d.at > 86400) return "";
  if (d.state === "waiting") {
    const seconds = Math.max(0, Math.ceil(d.retry_at - now / 1000));
    return seconds > 0 ? `Telegram просит подождать ещё ${seconds} с. Отправка повторится автоматически.`
      : "Telegram: время ожидания прошло; отправка повторяется автоматически.";
  }
  if (now / 1000 - d.at > 120) return "";
  if (d.state === "accepted" && d.retries) return "Telegram принял отправку после ожидания.";
  if (d.state === "cancelled") return "Отправка остановлена владельцем.";
  if (d.state === "failed") return "Доставка не подтверждена. Причина записана в журнале и результате отправки.";
  return "";
}

export function foldWords(st: FoldState, now = Date.now()) {
  const number = (n: number) => n.toLocaleString("ru-RU");
  const form = st.hot === null ? 'other' : new Intl.PluralRules('ru-RU').select(st.hot);
  const noun = ({one:'сообщение',few:'сообщения',many:'сообщений',other:'сообщения'} as Record<string,string>)[form];
  const counter = st.hot === null ? "Горячая память: число пока неизвестно" : `Горячая память: ${number(st.hot)} ${noun}`;
  const parts = [
    "Это свежие сообщения, которые ещё не вошли в сводку. Число рядом с названием чата считает весь архив.",
    `Обычная цель после свёртки — оставить ${number(st.keep)} последних сообщений. Большой объём текста может потребовать более короткого хвоста.`,
    st.offer_mode !== false
      ? `При ${number(st.offer_at)} сообщениях агент получает предложение свернуть память; при ${number(st.hard_at)} — предложение с отметкой жёсткого давления. Решение принимает агент. Ночная обработка также разбирает эти предложения.`
      : `При ${number(st.offer_at)} сообщениях свёртка возможна на границе разговора; при ${number(st.hard_at)} или превышении объёма начинается автоматически.`,
    st.offer_mode !== false && st.backstop_hours !== undefined
      ? `Если жёсткое давление остаётся без решения ${number(st.backstop_hours)} ч., включается автоматический запасной проход.` : "",
    "Один проход может обработать лишь часть накопившегося разговора. Переписка остаётся в архиве; сводку пишет агент своими словами.",
  ].filter(Boolean);
  let note = "";
  let state = "idle";
  if (st.pending || st.receipt?.state === "running") {
    note = "Идёт свёртка: агент пишет сводку своими словами. Переписка остаётся в архиве."; state = "running";
  } else if (st.receipt?.at && now - Date.parse(st.receipt.at) < 120_000) {
    state = st.receipt.state || "idle";
    note = state === "done" && st.receipt.folded !== undefined
      ? `В сводку перенесено сообщений: ${number(st.receipt.folded)}. Остальное остаётся в горячей памяти; переписка сохранена.`
      : state === "nothing" ? "В этом проходе сворачивать нечего. Переписка сохранена."
      : st.receipt.note || "";
  } else if (st.offer) {
    note = st.offer.hard ? "Агенту предложена свёртка: есть жёсткое давление на горячую память."
      : "Агенту предложено свернуть старую часть разговора.";
  }
  return { counter, explanation: parts.join(" "), note, state,
    canFold: state !== "running" && (st.hot !== null && st.hot > st.keep || !!st.offer) };
}
