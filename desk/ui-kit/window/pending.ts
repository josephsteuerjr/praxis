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
export interface FeedRow { text?: string; timestamp?: string; source_id?: string }
export interface SentBubble { text: string; at: string; source_id?: string }

/** Считать ли пузырь подтверждённым лентой своих реплик. */
export function confirmedByFeed(bubble: SentBubble, own: FeedRow[]): boolean {
  // Modern receipts are authoritative even if STT changes text or clocks differ.
  // Never fall back to text when this message has an identity.
  if (bubble.source_id) return own.some((m) => m.source_id === bubble.source_id);
  const text = bubble.text.trim();
  const since = Date.parse(bubble.at) - 5000;
  const fresh = (m: FeedRow) => !!m.timestamp && Number.isFinite(Date.parse(m.timestamp))
    && Number.isFinite(since) && Date.parse(m.timestamp) >= since;
  if (own.some((m) => fresh(m) && ((m.text || "").trim() === text
      || (m.text || "").trim().startsWith(text + "\n[")))) return true;
  if (!text.startsWith("[")) return false;
  const head = text.replace(/\]$/, "");
  return own.some((m) => {
    const row = (m.text || "").trim();
    if (!row.startsWith(head)) return false;
    if (!m.timestamp) return true;
    const at = Date.parse(m.timestamp);
    return Number.isNaN(at) || Number.isNaN(since) || at >= since;
  });
}
