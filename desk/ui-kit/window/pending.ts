/**
 * Пузырь «отправляется…» и лента: когда своя реплика лентой — это он и есть.
 *
 * Без импортов намеренно: правило проверяется стендом окна (app/test) как чистая
 * функция, а не через отрисовку.
 *
 * ⚠ 26.09, живой случай у мамы Егора: голосовое. Пузырь несёт заглушку «[голосовое]»,
 * а лента — «[голосовое]: расшифровка» (или «[голосовое не расшифровано: …]»), и сверка
 * слово в слово не сходилась никогда: четыре пузыря «агент читает сейчас» висели под
 * уже отвеченными репликами до перезапуска окна. Заглушку сверяем по началу и по
 * времени: своя реплика лентой не старше пузыря — подтверждение.
 */
export interface FeedRow { text?: string; timestamp?: string }
export interface SentBubble { text: string; at: string }

/** Считать ли пузырь подтверждённым лентой своих реплик. */
export function confirmedByFeed(bubble: SentBubble, own: FeedRow[]): boolean {
  const text = bubble.text.trim();
  if (own.some((m) => (m.text || "").trim() === text)) return true;
  if (!text.startsWith("[")) return false;
  const head = text.replace(/\]$/, "");
  const since = Date.parse(bubble.at) - 5000;
  return own.some((m) => {
    const row = (m.text || "").trim();
    if (!row.startsWith(head)) return false;
    if (!m.timestamp) return true;
    const at = Date.parse(m.timestamp);
    return Number.isNaN(at) || Number.isNaN(since) || at >= since;
  });
}
