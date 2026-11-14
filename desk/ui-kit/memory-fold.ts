import { t as uiText, locale } from "../lang/lang";
// Words follow the runtime policy; the archive count is a different quantity.
export interface FoldState {
  room: string; hot: number | null; keep: number; offer_at: number; hard_at: number;
  token_cap?: number; tape_chars?: number; offer_mode?: boolean; backstop_hours?: number;
  chars?: number; tokens?: number; owner_context?: boolean;
  policy_source?: string;
  operation?: { state: "queued" | "starting" | "running" | "interrupted" | "unconfirmed"; note: string } | null;
  paused?: boolean;
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
    return seconds > 0 ? uiText("ui.ui.kit.memory.fold.32eb0f3a11", {p0: String(seconds)})
      : uiText("ui.ui.kit.memory.fold.cf37d73b72");
  }
  if (now / 1000 - d.at > 120) return "";
  if (d.state === "accepted" && d.retries) return uiText("ui.ui.kit.memory.fold.8921a3848f");
  if (d.state === "cancelled") return uiText("ui.ui.kit.memory.fold.29e814a078");
  if (d.state === "failed") return uiText("ui.ui.kit.memory.fold.2035dce5ef");
  return "";
}

export function foldWords(st: FoldState, now = Date.now()) {
  const number = (n: number) => n.toLocaleString(locale());
  const form = st.hot === null ? 'other' : new Intl.PluralRules(locale()).select(st.hot);
  const noun = ({one:uiText("ui.ui.kit.memory.fold.8eb484df7b"),few:uiText("ui.ui.kit.memory.fold.e61740b9f7"),many:uiText("ui.ui.kit.memory.fold.30b3b3c0e6"),other:uiText("ui.ui.kit.memory.fold.e61740b9f7")} as Record<string,string>)[form];
  const counter = st.hot === null ? uiText("ui.ui.kit.memory.fold.231ed66209") : uiText("ui.ui.kit.memory.fold.4fe93ead9c", {p0: String(number(st.hot)), p1: String(noun)});
  const parts = [
    uiText("ui.ui.kit.memory.fold.9bfaec859d"),
    uiText("ui.ui.kit.memory.fold.df5aa0f6fa", {p0: String(number(st.keep))}),
    uiText("ui.ui.kit.memory.fold.3307f63bf9", {p0: String(number(st.offer_at)), p1: String(number(st.hard_at))}),
    uiText("ui.ui.kit.memory.fold.807e6dfd41"),
  ].filter(Boolean);
  let note = "";
  let state = "idle";
  if (st.operation) {
    state = st.operation.state; note = st.operation.note;
  } else if (st.pending || st.receipt?.state === "running") {
    // Older channels carry no live execution proof. A request is not execution.
    state = st.receipt?.state === "running" ? "unconfirmed" : "queued";
    note = state === "queued" ? uiText("ui.ui.kit.memory.fold.b28203b998")
      : uiText("ui.ui.kit.memory.fold.0a6ee725af");
  } else if (st.receipt?.at && now - Date.parse(st.receipt.at) < 120_000) {
    state = st.receipt.state || "idle";
    note = state === "done" && st.receipt.folded !== undefined
      ? uiText("ui.ui.kit.memory.fold.1bfa0020c1", {p0: String(number(st.receipt.folded))})
      : state === "nothing" ? uiText("ui.ui.kit.memory.fold.f7e82642d4")
      : st.receipt.note || "";
  } else if (st.offer) {
    note = st.offer.hard ? uiText("ui.ui.kit.memory.fold.a41a6dcab2")
      : uiText("ui.ui.kit.memory.fold.2b05153071");
  }
  return { counter, quantity: st.hot === null ? "" : number(st.hot) + " " + noun, explanation: parts.join(" "), note, state,
    canFold: false };
}
