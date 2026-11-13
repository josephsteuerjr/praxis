/** Validate the connection before saving: an unfinished server setup stays visible. */
export function collectConnection(out: Record<string, unknown>, draft: Record<string, unknown>): string {
  const base = String(draft.base || "").trim().replace(/\/+$/, "");
  const key = String(draft.key || "").trim();
  const remote = draft.mode === "remote";
  if (remote) {
    if (!base) return "Впиши адрес сервера, чтобы подключить к нему окно.";
    try {
      const url = new URL(base);
      if (!["http:", "https:"].includes(url.protocol) || !url.hostname || url.username || url.password || url.search || url.hash) throw new Error();
    } catch { return "Нужен адрес канала вида https://helene.example.com — без ключа, параметров и логина в ссылке."; }
    if (!key) return "Впиши ключ окна, который выдала установка Hélène на сервере.";
  }
  out.mode = remote ? "remote" : "local";
  if (base) out.base = base; else delete out.base;
  if (key) out.key = key; else delete out.key;
  return "";
}
