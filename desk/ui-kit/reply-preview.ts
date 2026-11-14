import { t as uiText } from "../lang/lang";
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
  const label = ({ photo: uiText("ui.ui.kit.reply.preview.5cea446a41"), image: uiText("ui.ui.kit.reply.preview.2a801d07ef"), video: uiText("ui.ui.kit.reply.preview.6e9d47cd62"), animation: "GIF", sticker: uiText("ui.ui.kit.reply.preview.1936a005f1"), voice: uiText("ui.ui.kit.reply.preview.96968066f1"), audio: uiText("ui.ui.kit.reply.preview.8c98423b90"), document: uiText("ui.ui.kit.reply.preview.48d8c3e9ee"), file: uiText("ui.ui.kit.reply.preview.48d8c3e9ee") } as Record<string, string>)[media] || media;
  const words = raw.replace(/\s+/g, " ").trim();
  return { id, author: original?.sender_name || row.reply_to_sender_name || uiText("ui.ui.kit.reply.preview.3e0ccd92fa"),
    text: words ? words.slice(0, 180) + (words.length > 180 ? "…" : "")
      : label ? uiText("ui.ui.kit.reply.preview.1c21d8676a", {p0: String(label)}) : uiText("ui.ui.kit.reply.preview.e1f7a3289f"),
    loaded: !!original };
}
