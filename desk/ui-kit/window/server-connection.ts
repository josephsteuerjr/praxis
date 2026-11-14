import { t as uiText } from "../../lang/lang";
/** Validate the connection before saving: an unfinished server setup stays visible. */
export function collectConnection(out: Record<string, unknown>, draft: Record<string, unknown>): string {
  const base = String(draft.base || "").trim().replace(/\/+$/, "");
  const key = String(draft.key || "").trim();
  const remote = draft.mode === "remote";
  if (remote) {
    if (!base) return uiText("ui.ui.kit.window.server.connection.36399506f0");
    try {
      const url = new URL(base);
      if (!["http:", "https:"].includes(url.protocol) || !url.hostname || url.username || url.password || url.search || url.hash) throw new Error();
    } catch { return uiText("ui.ui.kit.window.server.connection.c48b1a6dd3"); }
    if (!key) return uiText("ui.ui.kit.window.server.connection.7c1c727c84");
  }
  out.mode = remote ? "remote" : "local";
  if (base) out.base = base; else delete out.base;
  if (key) out.key = key; else delete out.key;
  return "";
}
