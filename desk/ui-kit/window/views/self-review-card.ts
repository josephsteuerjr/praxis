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

const depths = [
  { value: "quick" as const, title: "Быстро", text: "Изменённый код и явные регрессии" },
  { value: "normal" as const, title: "Обычно", text: "Также связи, читатели и тесты" },
  { value: "deep" as const, title: "Глубоко", text: "Также интеграция и аварийные пути" },
];
const defaults: Policy = { after_update: false, after_self_edit: false, depth: "normal", models: [], max_parallel: 1, max_iters: 0 };
const execution: Record<string, string> = {
  queued: "в очереди", starting: "запускается", running: "работает", finishing: "завершается",
  done: "завершено", error: "ошибка", unavailable: "недоступно", unknown: "состояние неизвестно",
  cancelled: "отменено", stopped: "остановлено", lost: "связь потеряна", stalled: "нет прогресса", failed: "сбой",
};
const decisions: Record<string, string> = {
  accepted: "Принять результат", fix: "Нужны исправления", deferred: "Отложить", "continue-with-reason": "Продолжить с обоснованием",
};

export function validateReviewPolicy(policy: Policy): string {
  if (!["quick", "normal", "deep"].includes(policy.depth)) return "Выбери глубину проверки.";
  if (!Number.isInteger(policy.max_parallel) || policy.max_parallel < 1 || policy.max_parallel > 8) return "Одновременно: целое число от 1 до 8.";
  if (!Number.isInteger(policy.max_iters) || policy.max_iters < 0) return "Шагов на критика: целое неотрицательное число; 0 — без ограничения.";
  if (policy.models.length > 8 || policy.models.some(model => !model.trim() || model.length > 200 || /:\/\/|::/.test(model))) return "Укажи не более 8 имён моделей текущего маршрута, без URL и provider::model.";
  return "";
}

export function reviewVerdict(review: Pick<Review, "verdict" | "execution">): string {
  if (review.execution !== "done") return "Вывод не подтверждён";
  return review.verdict === "clear" ? "Замечаний к предмету проверки нет" : review.verdict === "findings" ? "Есть замечания" : "Доказательств недостаточно";
}

export function selfReviewCard(context?: Pick<EditionContext, "draft" | "freshness">): HTMLElement {
  const body = el("div");
  body.style.overflowWrap = "anywhere";
  const node = card("Саморевью", body);
  node.dataset.selfReview = "";
  body.append(el("p", "field-hint", "Независимый взгляд на зафиксированный план, изменение или итог задачи — добровольный помощник. Критики читают точные материалы, не меняют код и не подтверждают эффекты одним описанием. Решение автора хранится отдельно от их вывода."));
  body.append(el("p", "notice", "Для совмещения изменённого агента при обновлении действует отдельный обязательный протокол: полная проверка состава перед независимым ревью. Обычное завершение без панели не открывает этот переход."));
  const health = el("p", "receipt", "Статус ещё не прочитан.");
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
    const picker = choice(depths, policy.depth, depth => edit({ depth }));
    picker.el.setAttribute("aria-label", "Глубина саморевью по умолчанию");
    const triggers = el("div", "form-grid");
    triggers.append(
      toggle("После обычного обновления", policy.after_update, value => edit({ after_update: value })),
      toggle("Автоматически после самоправок (по желанию)", policy.after_self_edit, value => edit({ after_self_edit: value })),
    );
    settingsBox.append(
      triggers, el("p", "field-label", "Глубина по умолчанию"), picker.el,
      field("Модели критиков", policy.models.join(", "), value => edit({ models: value.trim() ? [...new Set(value.split(",").map(model => model.trim()))] : [] }), {
        placeholder: "Пусто — текущая модель агента", mono: true,
        hint: "Через запятую, до 8 моделей текущего голосового маршрута. Это не подключение новых провайдеров. Глубокая проверка использует минимум двух критиков и расходует больше запросов.",
      }),
    );
    const limits = el("div", "form-grid two");
    limits.append(
      field("Одновременно", String(policy.max_parallel), value => edit({ max_parallel: value.trim() ? Number(value) : NaN }), { type: "number", hint: "От 1 до 8 критиков" }),
      field("Шагов на критика", String(policy.max_iters), value => edit({ max_iters: value.trim() ? Number(value) : NaN }), { type: "number", hint: "0 — без ограничения. Положительное число — явный предел шагов." }),
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
      if (fresh.schema !== "helene.self-review.status.v1" || !Array.isArray(fresh.reviews) || !Array.isArray(fresh.commands)) throw new Error("Канал не поддерживает этот формат саморевью.");
      status = fresh;
      uncertain = false;
      if (reset || !config) {
        if (!saved.ok || saved.mtime_ns == null || String(saved.mtime_ns) === "") throw new Error(saved.error || saved.why || "Нет отпечатка настроек; запись недоступна.");
        config = saved;
        policy = { ...defaults, ...saved.config.self_review, models: [...(saved.config.self_review?.models || [])] };
        dirty = false;
        drawSettings();
      }
      health.className = "receipt" + (!fresh.capability.available ? " err" : "");
      health.textContent = (fresh.capability.available ? "Саморевью доступно. " : "Сейчас саморевью недоступно. ") + (fresh.capability.reason || "");
      if (fresh.runtime?.self_edit_supported === false) health.textContent += " Автоматическое ревью самоправок не поддержано выбранным кодом агента: " + (fresh.runtime.self_edit_note || "нет callback API");
      if (fresh.runtime?.error) health.textContent += " " + fresh.runtime.error;
      drawReviews();
      commands.replaceChildren(el("h4", "", "Команды"));
      if (!fresh.commands.length) commands.append(el("p", "field-hint", "Команд пока нет."));
      for (const command of fresh.commands) {
        const line = el("p", "field-hint", `${command.command_id} · ${command.action || "команда"} · ${execution[command.state] || command.state}${command.review_id ? " · " + command.review_id : ""}${command.error ? " · " + command.error : ""}`);
        line.style.overflowWrap = "anywhere";
        commands.append(line);
      }
      say(reset ? "Настройки перечитаны. Несохранённый ввод этой карточки сброшен." : "Статус перечитан. Результаты команд — в списке ниже.");
    } catch (error) {
      if (!node.isConnected) return;
      status = null;
      health.className = "receipt err";
      health.textContent = "Не удалось прочитать саморевью: " + humanError(error).text + " Повтори обновление статуса.";
    } finally { loading = false; sync(); }
  }
  async function submit(body: Record<string, unknown>) {
    if (busy || uncertain || !status?.capability.available) return;
    busy = true;
    sync();
    say("Передаю команду…");
    try {
      const answer = await post<Command & { ok: boolean }>("/api/self-review", body);
      if (!node.isConnected) return;
      if (!answer.ok || !answer.command_id || answer.state !== "queued") throw new Error("Нет подтверждённой квитанции команды.");
      uncertain = true;
      say(`Команда ${answer.command_id} в очереди. Это ещё не выполненное действие и не одобрение. Нажми «Обновить статус».`);
    } catch (error) {
      if (!node.isConnected) return;
      uncertain = true;
      say("Результат передачи не подтверждён: " + humanError(error).text + " Перед повтором обнови статус и проверь список команд.", true);
    } finally { busy = false; sync(); }
  }
  function drawReviews() {
    actions.splice(1);
    reviews.replaceChildren(el("h4", "", "Проверки исходников"));
    if (!status?.reviews.length) reviews.append(el("p", "field-hint", "Проверок пока нет. Можно запросить первую ниже."));
    for (const review of status?.reviews || []) {
      const box = el("details");
      box.open = review.required || review.state === "needs_attention" || review.state === "pending";
      box.append(el("summary", "field-label", `${review.required ? "Обязательная · " : ""}${review.id} · ${execution[review.execution] || review.execution} · ${reviewVerdict(review)}`));
      box.append(el("p", "field-hint", `Состояние: ${review.state} · причина: ${review.trigger || "по запросу"}`));
      const subject = el("p", "mono", review.subject_sha256);
      subject.style.overflowWrap = "anywhere";
      box.append(el("p", "field-label", "SHA-256 снимка"), subject);
      if (review.error) box.append(el("p", "receipt err", review.error));
      if (review.gaps?.length) box.append(el("p", "receipt err", "Пробелы материалов: " + JSON.stringify(review.gaps)));
      for (const critic of review.critics || []) {
        box.append(el("p", "field-label", `${critic.model_actual || critic.model || "Текущая модель"} · ${execution[critic.execution || "unknown"] || critic.execution} · ${critic.verdict || "вывод не получен"}`));
        if (critic.summary || critic.reason) box.append(el("p", "field-hint", [critic.summary, critic.reason].filter(Boolean).join(" · ")));
        for (const finding of critic.findings || []) box.append(el("p", "field-hint", [finding.path, finding.scenario, finding.detail].filter(Boolean).join(" · ")));
        if (critic.gaps?.length) box.append(el("p", "field-hint", "Пробелы критика: " + JSON.stringify(critic.gaps)));
      }
      for (const decision of review.dispositions || []) box.append(el("p", "receipt", `Решение владельца: ${decisions[decision.decision] || decision.decision} · ${decision.reason}${decision.stale ? " · исходники уже изменились" : ""}`));
      if (["queued", "starting", "running", "finishing", "unknown"].includes(review.execution)) {
        const cancel = button("Запросить отмену проверки", "quiet", () => void submit({ action: "cancel", id: review.id }));
        box.append(cancel);
        actions.push(cancel);
      }
      const decision = el("select", "field-input");
      decision.setAttribute("aria-label", "Решение по " + review.id);
      for (const [value, label] of Object.entries(decisions)) {
        const option = el("option", "", label);
        option.value = value;
        option.disabled = value === "accepted" && (review.execution !== "done" || review.verdict !== "clear");
        decision.append(option);
      }
      decision.value = review.execution === "done" && review.verdict === "clear" ? "accepted" : "fix";
      let reason = "";
      const reasonField = field("Обоснование решения", "", value => { reason = value; });
      const decide = button("Записать решение", "quiet", () => {
        if (!reason.trim()) { say("Добавь обоснование: вывод критика не заменяет твоё решение.", true); return; }
        void submit({ action: "disposition", id: review.id, decision: decision.value, reason: reason.trim(), subject_sha256: review.subject_sha256 });
      });
      actions.push(decide);
      box.append(decision, reasonField, decide);
      reviews.append(box);
    }
  }
  const save = button("Сохранить саморевью", "quiet", async () => {
    if (!config || busy || loading || !dirty) return;
    const error = validateReviewPolicy(policy);
    if (error) { say(error, true); return; }
    const stamp = String(config.mtime_ns);
    const selected = structuredClone(policy);
    const editing = revision;
    busy = true;
    sync();
    say("Сохраняю настройки саморевью…");
    try {
      const answer = await post<{ ok: boolean; mtime_ns?: string | number; error?: string }>("/api/agent-config", { config: { self_review: selected }, mtime_ns: stamp });
      if (!node.isConnected) return;
      if (!answer.ok || answer.mtime_ns == null) throw new Error(answer.error || "Запись не подтверждена. Перечитай настройки перед повтором.");
      config = { ...config, config: { ...config.config, self_review: selected }, mtime_ns: answer.mtime_ns };
      if (context && context.draft.mode !== "remote") {
        context.draft.self_review = selected;
        context.freshness.accept?.(stamp, String(answer.mtime_ns));
      }
      dirty = editing !== revision;
      say("Настройки саморевью сохранены; агент перечитает их на своём следующем тике." + (dirty ? " Более поздний ввод ещё не сохранён." : ""));
    } catch (error) {
      if (node.isConnected) say(humanError(error).text + " Ввод сохранён на экране; при конфликте перечитай настройки и перенеси правку заново.", true);
    } finally { busy = false; sync(); }
  });
  const refresh = button("Обновить статус", "quiet", () => void load());
  const reload = button("Перечитать настройки (сбросить ввод)", "quiet", () => void load(true));
  const controls = el("div", "actions");
  controls.append(save, reload);
  const requestBox = el("div");
  let depth: Depth = "normal";
  const requestDepth = choice(depths, depth, value => { depth = value; });
  requestDepth.el.setAttribute("aria-label", "Глубина новой проверки");
  let intent = "";
  let retry = false;
  const request = button("Запросить ревью", "primary", () => void submit({ action: "request", depth, intent: intent.trim(), retry }));
  actions.push(request);
  const requestActions = el("div", "actions");
  requestActions.append(request, refresh);
  requestBox.append(el("h4", "", "Новая проверка"), requestDepth.el,
    field("Что проверить", "", value => { intent = value; }, { hint: "Цель и спорные места. Проверка расходует запросы выбранного маршрута агента." }),
    toggle("Повторить уже завершённую проверку тех же исходников", retry, value => { retry = value; }), requestActions);
  body.append(health, requestBox, receipt, el("h4", "", "Автоматические проверки"), settingsBox, controls, reviews, commands);
  drawSettings();
  sync();
  queueMicrotask(() => { if (node.isConnected) void load(); });
  return node;
}
