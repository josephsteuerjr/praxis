export interface ReplyRow {
  source_message_id?: string; text?: string; sender_name?: string;
  reply_to_message_id?: number; reply_to_sender_name?: string;
  reply_to_text?: string; reply_to_media?: string; media?: string; media_kind?: string;
}

export function replyPreview(row: ReplyRow, loaded: Map<string, ReplyRow>) {
  if (!row.reply_to_message_id) return null;
  const id = String(row.reply_to_message_id);
  const original = loaded.get(id);
  const raw = original ? original.text || "" : row.reply_to_text || "";
  const media = original?.media || original?.media_kind || row.reply_to_media || "";
  const label = ({ photo: "фото", image: "картинка", video: "видео", animation: "GIF", sticker: "стикер", voice: "голосовое сообщение", audio: "аудио", document: "файл", file: "файл" } as Record<string, string>)[media] || media;
  const words = raw.replace(/\s+/g, " ").trim();
  return { id, author: original?.sender_name || row.reply_to_sender_name || "Сообщение",
    text: words ? words.slice(0, 180) + (words.length > 180 ? "…" : "")
      : label ? `Вложение: ${label}` : "Исходное сообщение ещё не загружено",
    loaded: !!original };
}
