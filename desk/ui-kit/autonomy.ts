import { t as uiText } from "../lang/lang";
import { esc } from "./text";

export interface AutonomyState {
  supported: boolean; available: boolean; paused: boolean; stopped: boolean | null;
  pid?: number | null; started?: string | null; note: string;
  pending?: { id?: string; action?: string } | null;
  receipt?: { id?: string; action?: string; done?: boolean; note?: string; phase?: string } | null;
}

export function autonomyWords(state: AutonomyState) {
  return { action: state.paused ? "resume" : "pause", label: state.paused ? uiText("ui.ui.kit.autonomy.a42aee58d6") : uiText("ui.ui.kit.autonomy.65530fd463"),
    title: state.paused ? uiText("ui.ui.kit.autonomy.1a142cbea7")
      : uiText("ui.ui.kit.autonomy.605649c4db"),
    disabled: !state.available || state.stopped === true || !!state.pending,
    note: state.stopped ? uiText("ui.ui.kit.autonomy.a9d6e7647a") : state.pending
      ? uiText("ui.ui.kit.autonomy.caf91b5daf") : state.note };
}

/** Works through the selected agent's HTTP/WS channel, including server agents. */
export function mountAutonomy(box: HTMLElement, io: {
  read: () => Promise<AutonomyState>;
  write: (action: string) => Promise<{ ok: boolean; note: string; request?: { id: string } }>;
  identity: () => string; buttonClass?: string; compact?: boolean; onStateChange?: (state: AutonomyState) => void;
}) {
  let observed: AutonomyState | null = null, reading = "", sending = "", message = "", requestId = "", identity=io.identity();
  let generation=0, flight: object | null=null;
  const syncIdentity = () => {
    const next=io.identity();
    if (next!==identity) { identity=next; generation++; observed=null; reading=""; sending=""; requestId=""; message=""; flight=null; box.replaceChildren(); }
  };
  const paint = () => {
    if (!box.isConnected || !observed) return;
    const words = autonomyWords(observed);
    const receipt = requestId && observed.receipt?.id === requestId && observed.receipt.phase !== "resume_prepared" ? observed.receipt : null;
    if (receipt) { message = receipt.note || (receipt.done ? observed.note : uiText("ui.ui.kit.autonomy.59b6e570ab")); requestId = ""; }
    const note=io.compact ? observed.stopped ? uiText("ui.ui.kit.autonomy.39e74d43c1") : observed.pending ? uiText("ui.ui.kit.autonomy.6f6bba56e5") : observed.paused ? uiText("ui.ui.kit.autonomy.7c68889f13") : !observed.available ? uiText("ui.ui.kit.autonomy.91ec39acbb") : "" : message || words.note;
    box.innerHTML = `<button type="button" class="${esc(io.buttonClass || "btn quiet")}" data-autonomy-action="${words.action}"${words.disabled || sending ? " disabled" : ""} title="${esc(words.disabled ? words.note : words.title)}">${esc(words.label)}</button><span class="muted" aria-live="polite" data-autonomy-note>${esc(note)}</span>`;
  };
  async function refresh() {
    if (!box.isConnected || document.hidden) return;
    syncIdentity(); const id=io.identity(),epoch=generation; if (reading===id) return; reading=id;
    const current={}; flight=current;
    try {
      const next=await io.read(); if (id!==io.identity() || epoch!==generation) return;
      if (observed && observed.paused!==next.paused) {
        if (!requestId) message="";
        io.onStateChange?.(next);
      }
      observed=next; paint();
    } catch {
      if (id!==io.identity() || epoch!==generation) return;
      observed={supported:false,available:false,paused:observed?.paused || false,stopped:null,note:uiText("ui.ui.kit.autonomy.604f949284")}; paint();
    } finally { if (flight===current) { reading=""; flight=null; } }
  }
  box.addEventListener("click", async event => {
    const button=(event.target as HTMLElement).closest<HTMLButtonElement>("[data-autonomy-action]");
    if (identity!==io.identity()) { syncIdentity(); void refresh(); return; }
    if (!button || button.disabled || !observed || sending) return;
    const id=io.identity(),epoch=generation,action=button.dataset.autonomyAction!; sending=id; message=uiText("ui.ui.kit.autonomy.71f9b0bcbe"); paint();
    try {
      const answer=await io.write(action); if (id!==io.identity() || epoch!==generation) return;
      message=answer.note; requestId=answer.ok ? answer.request?.id || "" : "";
    } catch { if (id===io.identity() && epoch===generation) message=uiText("ui.ui.kit.autonomy.0ee88d14e9"); }
    finally { if (sending===id && epoch===generation) sending=""; if (id===io.identity() && epoch===generation) { paint(); void refresh(); } }
  });
  const timer=window.setInterval(() => {
    if (!box.isConnected) { clearInterval(timer); return; }
    syncIdentity();
    void refresh();
  },2000);
  void refresh();
  return () => clearInterval(timer);
}
