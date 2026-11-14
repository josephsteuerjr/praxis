import { t as uiText } from "../../../lang/lang";
import { button, card, choice, el, field, toggle } from "../../dom";
import { api, post } from "../api";
import { humanError } from "../lib";
import type { EditionContext } from "./settings-frame";

type Depth = "quick" | "normal" | "deep";
interface Policy {
  after_update: boolean;
  after_self_edit: boolean;
  depth: Depth;
  models: string[];
  max_parallel: number;
  max_iters: number;
  [key: string]: unknown;
}
interface Critic {
  unit_id?: string;
  model?: string;
  model_actual?: string;
  execution?: string;
  verdict?: string;
  summary?: string;
  reason?: string;
  findings?: Array<{ path?: string; scenario?: string; detail?: string }>;
  gaps?: unknown[];
}
interface Review {
  id: string;
  required?: boolean;
  trigger?: string;
  execution: string;
  state: string;
  verdict: string;
  subject_sha256: string;
  critics?: Critic[];
  gaps?: unknown[];
  error?: string;
  dispositions?: Array<{ decision: string; reason: string; stale?: boolean }>;
}
interface Command {
  command_id: string;
  state: string;
  action?: string;
  review_id?: string;
  error?: string;
}
interface Status {
  schema: string;
  settings: Policy;
  reviews: Review[];
  commands: Command[];
  capability: { supported: boolean; available: boolean; reason?: string };
  runtime?: { self_edit_supported?: boolean; self_edit_note?: string; error?: string };
}
interface ConfigReply {
  ok: boolean;
  config: { self_review?: Policy };
  mtime_ns?: string | number;
  error?: string;
  why?: string;
}

const depths = () => [
  { value: "quick" as const, title: uiText("ui.ui.kit.window.views.self.review.card.815ccca6b5"), text: uiText("ui.ui.kit.window.views.self.review.card.06945f9753") },
  { value: "normal" as const, title: uiText("ui.ui.kit.window.views.self.review.card.648ccc6217"), text: uiText("ui.ui.kit.window.views.self.review.card.4f7e888a5b") },
  { value: "deep" as const, title: uiText("ui.ui.kit.window.views.self.review.card.1e275f16d9"), text: uiText("ui.ui.kit.window.views.self.review.card.1f706f4468") },
];
const defaults: Policy = { after_update: false, after_self_edit: false, depth: "normal", models: [], max_parallel: 1, max_iters: 0 };
const execution: Record<string, string> = {
  get queued() { return uiText("ui.ui.kit.window.views.self.review.card.f8ba02ed06"); }, get starting() { return uiText("ui.ui.kit.window.views.self.review.card.4c9118412a"); }, get running() { return uiText("ui.ui.kit.window.views.self.review.card.c01295237e"); }, get finishing() { return uiText("ui.ui.kit.window.views.self.review.card.e9a207e8f0"); },
  get done() { return uiText("ui.ui.kit.window.views.self.review.card.5f9b6607de"); }, get error() { return uiText("ui.ui.kit.window.views.self.review.card.44d4090ae9"); }, get unavailable() { return uiText("ui.ui.kit.window.views.self.review.card.2420cd2be1"); }, get unknown() { return uiText("ui.ui.kit.window.views.self.review.card.21be7b29a1"); },
  get cancelled() { return uiText("ui.ui.kit.window.views.self.review.card.8a09723017"); }, get stopped() { return uiText("ui.ui.kit.window.views.self.review.card.9ab567d8cc"); }, get lost() { return uiText("ui.ui.kit.window.views.self.review.card.8c77eb5f7f"); }, get stalled() { return uiText("ui.ui.kit.window.views.self.review.card.e931d8f8db"); }, get failed() { return uiText("ui.ui.kit.window.views.self.review.card.b15413deaf"); },
};
const decisions: Record<string, string> = {
  get accepted() { return uiText("ui.ui.kit.window.views.self.review.card.9315c4ed66"); }, get fix() { return uiText("ui.ui.kit.window.views.self.review.card.125dea6404"); }, get deferred() { return uiText("ui.ui.kit.window.views.self.review.card.255ada037f"); }, get "continue-with-reason"() { return uiText("ui.ui.kit.window.views.self.review.card.0de7457445"); },
};

export function validateReviewPolicy(policy: Policy): string {
  if (!["quick", "normal", "deep"].includes(policy.depth)) return uiText("ui.ui.kit.window.views.self.review.card.585571a3e9");
  if (!Number.isInteger(policy.max_parallel) || policy.max_parallel < 1 || policy.max_parallel > 8) return uiText("ui.ui.kit.window.views.self.review.card.42fdac27b0");
  if (!Number.isInteger(policy.max_iters) || policy.max_iters < 0) return uiText("ui.ui.kit.window.views.self.review.card.faa9e30cd2");
  if (policy.models.length > 8 || policy.models.some(model => !model.trim() || model.length > 200 || /:\/\/|::/.test(model))) return uiText("ui.ui.kit.window.views.self.review.card.e7ea863e95");
  return "";
}

export function reviewVerdict(review: Pick<Review, "verdict" | "execution">): string {
  if (review.execution !== "done") return uiText("ui.ui.kit.window.views.self.review.card.b0dc500204");
  return review.verdict === "clear" ? uiText("ui.ui.kit.window.views.self.review.card.056904aec7") : review.verdict === "findings" ? uiText("ui.ui.kit.window.views.self.review.card.826964e603") : uiText("ui.ui.kit.window.views.self.review.card.8d368e1de4");
}

export function selfReviewCard(context?: Pick<EditionContext, "draft" | "freshness">): HTMLElement {
  const body = el("div");
  body.style.overflowWrap = "anywhere";
  const node = card(uiText("ui.ui.kit.window.views.self.review.card.4180b5bea1"), body);
  node.dataset.selfReview = "";
  body.append(el("p", "field-hint", uiText("ui.ui.kit.window.views.self.review.card.b8c9b7818c")));
  body.append(el("p", "notice", uiText("ui.ui.kit.window.views.self.review.card.f020352f5e")));
  const health = el("p", "receipt", uiText("ui.ui.kit.window.views.self.review.card.012a783999"));
  health.setAttribute("aria-live", "polite");
  const receipt = el("p", "receipt");
  receipt.setAttribute("aria-live", "polite");
  const settingsBox = el("div");
  const reviews = el("div");
  const commands = el("div");
  let status: Status | null = null;
  let config: ConfigReply | null = null;
  let policy: Policy = { ...defaults, models: [] };
  let dirty = false;
  let loading = false;
  let busy = false;
  let uncertain = false;
  let revision = 0;
  const actions: HTMLButtonElement[] = [];
  const say = (text: string, error = false) => {
    receipt.className = "receipt" + (error ? " err" : "");
    receipt.textContent = text;
  };
  const sync = () => {
    for (const action of actions) action.disabled = busy || !status?.capability.available || uncertain;
    save.disabled = busy || loading || !config?.mtime_ns || !dirty;
    refresh.disabled = busy || loading;
    reload.disabled = busy || loading;
  };
  function drawSettings() {
    settingsBox.replaceChildren();
    const edit = (patch: Partial<Policy>) => {
      policy = { ...policy, ...patch };
      dirty = true;
      revision++;
      sync();
    };
    const picker = choice(depths(), policy.depth, depth => edit({ depth }));
    picker.el.setAttribute("aria-label", uiText("ui.ui.kit.window.views.self.review.card.0cff373bfa"));
    const triggers = el("div", "form-grid");
    triggers.append(
      toggle(uiText("ui.ui.kit.window.views.self.review.card.ee703af094"), policy.after_update, value => edit({ after_update: value })),
      toggle(uiText("ui.ui.kit.window.views.self.review.card.b48cc2374a"), policy.after_self_edit, value => edit({ after_self_edit: value })),
    );
    settingsBox.append(
      triggers, el("p", "field-label", uiText("ui.ui.kit.window.views.self.review.card.49c610883f")), picker.el,
      field(uiText("ui.ui.kit.window.views.self.review.card.f5452206f0"), policy.models.join(", "), value => edit({ models: value.trim() ? [...new Set(value.split(",").map(model => model.trim()))] : [] }), {
        placeholder: uiText("ui.ui.kit.window.views.self.review.card.98fa8a9347"), mono: true,
        hint: uiText("ui.ui.kit.window.views.self.review.card.371f362d8e"),
      }),
    );
    const limits = el("div", "form-grid two");
    limits.append(
      field(uiText("ui.ui.kit.window.views.self.review.card.016f6bb06f"), String(policy.max_parallel), value => edit({ max_parallel: value.trim() ? Number(value) : NaN }), { type: "number", hint: uiText("ui.ui.kit.window.views.self.review.card.129c792dd8") }),
      field(uiText("ui.ui.kit.window.views.self.review.card.ff557245ca"), String(policy.max_iters), value => edit({ max_iters: value.trim() ? Number(value) : NaN }), { type: "number", hint: uiText("ui.ui.kit.window.views.self.review.card.458244f693") }),
    );
    settingsBox.append(limits);
  }
  async function load(reset = false) {
    if (loading || busy) return;
    loading = true;
    sync();
    try {
      const [fresh, saved] = await Promise.all([
        api<Status>("/api/self-review"), api<ConfigReply>("/api/agent-config"),
      ]);
      if (!node.isConnected) return;
      if (fresh.schema !== "helene.self-review.status.v1" || !Array.isArray(fresh.reviews) || !Array.isArray(fresh.commands)) throw new Error(uiText("ui.ui.kit.window.views.self.review.card.0d5b85e251"));
      status = fresh;
      uncertain = false;
      if (reset || !config) {
        if (!saved.ok || saved.mtime_ns == null || String(saved.mtime_ns) === "") throw new Error(saved.error || saved.why || uiText("ui.ui.kit.window.views.self.review.card.82ed5b0c03"));
        config = saved;
        policy = { ...defaults, ...saved.config.self_review, models: [...(saved.config.self_review?.models || [])] };
        dirty = false;
        drawSettings();
      }
      health.className = "receipt" + (!fresh.capability.available ? " err" : "");
      health.textContent = (fresh.capability.available ? uiText("ui.ui.kit.window.views.self.review.card.eafa4be224") : uiText("ui.ui.kit.window.views.self.review.card.3a105cf69d")) + (fresh.capability.reason || "");
      if (fresh.runtime?.self_edit_supported === false) health.textContent += uiText("ui.ui.kit.window.views.self.review.card.3e6115ec10") + (fresh.runtime.self_edit_note || uiText("ui.ui.kit.window.views.self.review.card.12d977872f"));
      if (fresh.runtime?.error) health.textContent += " " + fresh.runtime.error;
      drawReviews();
      commands.replaceChildren(el("h4", "", uiText("ui.ui.kit.window.views.self.review.card.fb0c02331f")));
      if (!fresh.commands.length) commands.append(el("p", "field-hint", uiText("ui.ui.kit.window.views.self.review.card.b786cc1f98")));
      for (const command of fresh.commands) {
        const line = el("p", "field-hint", `${command.command_id} · ${command.action || uiText("ui.ui.kit.window.views.self.review.card.8e1afdee43")} · ${execution[command.state] || command.state}${command.review_id ? " · " + command.review_id : ""}${command.error ? " · " + command.error : ""}`);
        line.style.overflowWrap = "anywhere";
        commands.append(line);
      }
      say(reset ? uiText("ui.ui.kit.window.views.self.review.card.23b6854591") : uiText("ui.ui.kit.window.views.self.review.card.456206917c"));
    } catch (error) {
      if (!node.isConnected) return;
      status = null;
      health.className = "receipt err";
      health.textContent = uiText("ui.ui.kit.window.views.self.review.card.3bc683c8a0") + humanError(error).text + uiText("ui.ui.kit.window.views.self.review.card.68e2775123");
    } finally { loading = false; sync(); }
  }
  async function submit(body: Record<string, unknown>) {
    if (busy || uncertain || !status?.capability.available) return;
    busy = true;
    sync();
    say(uiText("ui.ui.kit.window.views.self.review.card.71f9b0bcbe"));
    try {
      const answer = await post<Command & { ok: boolean }>("/api/self-review", body);
      if (!node.isConnected) return;
      if (!answer.ok || !answer.command_id || answer.state !== "queued") throw new Error(uiText("ui.ui.kit.window.views.self.review.card.3846f9ad18"));
      uncertain = true;
      say(uiText("ui.ui.kit.window.views.self.review.card.ae0866b534", {p0: String(answer.command_id)}));
    } catch (error) {
      if (!node.isConnected) return;
      uncertain = true;
      say(uiText("ui.ui.kit.window.views.self.review.card.5c7d157c89") + humanError(error).text + uiText("ui.ui.kit.window.views.self.review.card.7add11cddc"), true);
    } finally { busy = false; sync(); }
  }
  function drawReviews() {
    actions.splice(1);
    reviews.replaceChildren(el("h4", "", uiText("ui.ui.kit.window.views.self.review.card.b39dd90e6d")));
    if (!status?.reviews.length) reviews.append(el("p", "field-hint", uiText("ui.ui.kit.window.views.self.review.card.e595c960b8")));
    for (const review of status?.reviews || []) {
      const box = el("details");
      box.open = review.required || review.state === "needs_attention" || review.state === "pending";
      box.append(el("summary", "field-label", `${review.required ? uiText("ui.ui.kit.window.views.self.review.card.ddf2428e1a") : ""}${review.id} · ${execution[review.execution] || review.execution} · ${reviewVerdict(review)}`));
      box.append(el("p", "field-hint", uiText("ui.ui.kit.window.views.self.review.card.451ec6ca11", {p0: String(review.state), p1: String(review.trigger || uiText("ui.ui.kit.window.views.self.review.card.67fb4ce8d7"))})));
      const subject = el("p", "mono", review.subject_sha256);
      subject.style.overflowWrap = "anywhere";
      box.append(el("p", "field-label", uiText("ui.ui.kit.window.views.self.review.card.b1a89a8cdf")), subject);
      if (review.error) box.append(el("p", "receipt err", review.error));
      if (review.gaps?.length) box.append(el("p", "receipt err", uiText("ui.ui.kit.window.views.self.review.card.c900bc3636") + JSON.stringify(review.gaps)));
      for (const critic of review.critics || []) {
        box.append(el("p", "field-label", `${critic.model_actual || critic.model || uiText("ui.ui.kit.window.views.self.review.card.9ab5d54c24")} · ${execution[critic.execution || "unknown"] || critic.execution} · ${critic.verdict || uiText("ui.ui.kit.window.views.self.review.card.3ece4344bf")}`));
        if (critic.summary || critic.reason) box.append(el("p", "field-hint", [critic.summary, critic.reason].filter(Boolean).join(" · ")));
        for (const finding of critic.findings || []) box.append(el("p", "field-hint", [finding.path, finding.scenario, finding.detail].filter(Boolean).join(" · ")));
        if (critic.gaps?.length) box.append(el("p", "field-hint", uiText("ui.ui.kit.window.views.self.review.card.218399e6b0") + JSON.stringify(critic.gaps)));
      }
      for (const decision of review.dispositions || []) box.append(el("p", "receipt", uiText("ui.ui.kit.window.views.self.review.card.8a8e4ed864", {p0: String(decisions[decision.decision] || decision.decision), p1: String(decision.reason), p2: String(decision.stale ? uiText("ui.ui.kit.window.views.self.review.card.0a24c63afa") : "")})));
      if (["queued", "starting", "running", "finishing", "unknown"].includes(review.execution)) {
        const cancel = button(uiText("ui.ui.kit.window.views.self.review.card.addfc86959"), "quiet", () => void submit({ action: "cancel", id: review.id }));
        box.append(cancel);
        actions.push(cancel);
      }
      const decision = el("select", "field-input");
      decision.setAttribute("aria-label", uiText("ui.ui.kit.window.views.self.review.card.857c16fd72") + review.id);
      for (const [value, label] of Object.entries(decisions)) {
        const option = el("option", "", label);
        option.value = value;
        option.disabled = value === "accepted" && (review.execution !== "done" || review.verdict !== "clear");
        decision.append(option);
      }
      decision.value = review.execution === "done" && review.verdict === "clear" ? "accepted" : "fix";
      let reason = "";
      const reasonField = field(uiText("ui.ui.kit.window.views.self.review.card.2e22ac604b"), "", value => { reason = value; });
      const decide = button(uiText("ui.ui.kit.window.views.self.review.card.97e03fc011"), "quiet", () => {
        if (!reason.trim()) { say(uiText("ui.ui.kit.window.views.self.review.card.e37aefbc22"), true); return; }
        void submit({ action: "disposition", id: review.id, decision: decision.value, reason: reason.trim(), subject_sha256: review.subject_sha256 });
      });
      actions.push(decide);
      box.append(decision, reasonField, decide);
      reviews.append(box);
    }
  }
  const save = button(uiText("ui.ui.kit.window.views.self.review.card.fbc739485e"), "quiet", async () => {
    if (!config || busy || loading || !dirty) return;
    const error = validateReviewPolicy(policy);
    if (error) { say(error, true); return; }
    const stamp = String(config.mtime_ns);
    const selected = structuredClone(policy);
    const editing = revision;
    busy = true;
    sync();
    say(uiText("ui.ui.kit.window.views.self.review.card.0aa2473337"));
    try {
      const answer = await post<{ ok: boolean; mtime_ns?: string | number; error?: string }>("/api/agent-config", { config: { self_review: selected }, mtime_ns: stamp });
      if (!node.isConnected) return;
      if (!answer.ok || answer.mtime_ns == null) throw new Error(answer.error || uiText("ui.ui.kit.window.views.self.review.card.aa6e40507b"));
      config = { ...config, config: { ...config.config, self_review: selected }, mtime_ns: answer.mtime_ns };
      if (context && context.draft.mode !== "remote") {
        context.draft.self_review = selected;
        context.freshness.accept?.(stamp, String(answer.mtime_ns));
      }
      dirty = editing !== revision;
      say(uiText("ui.ui.kit.window.views.self.review.card.c25112a6f7") + (dirty ? uiText("ui.ui.kit.window.views.self.review.card.e817fd5110") : ""));
    } catch (error) {
      if (node.isConnected) say(humanError(error).text + uiText("ui.ui.kit.window.views.self.review.card.5ad31efa23"), true);
    } finally { busy = false; sync(); }
  });
  const refresh = button(uiText("ui.ui.kit.window.views.self.review.card.83553fb558"), "quiet", () => void load());
  const reload = button(uiText("ui.ui.kit.window.views.self.review.card.2f863dd387"), "quiet", () => void load(true));
  const controls = el("div", "actions");
  controls.append(save, reload);
  const requestBox = el("div");
  let depth: Depth = "normal";
  const requestDepth = choice(depths(), depth, value => { depth = value; });
  requestDepth.el.setAttribute("aria-label", uiText("ui.ui.kit.window.views.self.review.card.83315e730d"));
  let intent = "";
  let retry = false;
  const request = button(uiText("ui.ui.kit.window.views.self.review.card.374cedfdd9"), "primary", () => void submit({ action: "request", depth, intent: intent.trim(), retry }));
  actions.push(request);
  const requestActions = el("div", "actions");
  requestActions.append(request, refresh);
  requestBox.append(el("h4", "", uiText("ui.ui.kit.window.views.self.review.card.5e4a8f3db6")), requestDepth.el,
    field(uiText("ui.ui.kit.window.views.self.review.card.20a58b8729"), "", value => { intent = value; }, { hint: uiText("ui.ui.kit.window.views.self.review.card.c743da0c10") }),
    toggle(uiText("ui.ui.kit.window.views.self.review.card.e9718493fd"), retry, value => { retry = value; }), requestActions);
  body.append(health, requestBox, receipt, el("h4", "", uiText("ui.ui.kit.window.views.self.review.card.e3d71b9690")), settingsBox, controls, reviews, commands);
  drawSettings();
  sync();
  queueMicrotask(() => { if (node.isConnected) void load(); });
  return node;
}
