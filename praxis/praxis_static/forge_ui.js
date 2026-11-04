const mounted = new WeakMap();
const node = (tag, text = "", className = "") => {
  const value = document.createElement(tag);
  value.textContent = text;
  value.className = className;
  return value;
};
const button = (label, action) => {
  const value = node("button", label, "forge-button");
  value.type = "button";
  value.addEventListener("click", async () => {
    try { await action(); }
    catch (error) { value.closest(".forge-panel")?.querySelector("[role=status]")?.replaceChildren(document.createTextNode(error.message)); }
  });
  return value;
};

export function mountForge(host, { api, owner, stale = false }) {
  if (!host) return;
  host.hidden = !owner;
  if (!owner) { host.replaceChildren(); mounted.delete(host); return; }
  let state = mounted.get(host);
  if (!state) {
    state = { selected: "", last: 0, busy: false, cursor: "", stale, outputs: new Map() };
    const heading = node("header");
    heading.append(node("h2", "Команда Праксис"));
    const status = node("p", "Получаю состояние команды…");
    status.setAttribute("role", "status");
    const list = node("div", "", "forge-teams");
    const detail = node("div", "", "forge-detail");
    host.replaceChildren(heading, status, list, detail);
    state.status = status;
    state.list = list;
    state.detail = detail;
    state.api = async (action, fields = {}) => {
      const reply = await api("/command", { method: "POST", body: { domain: "forge", action, ...fields } });
      return reply.forge;
    };
    state.control = async (task, unit, action) => {
      if (state.stale) throw new Error("Сначала обновите соединение с Праксис");
      const command_id = crypto.randomUUID().replaceAll("-", "");
      const receipt = await state.api(action, { task_id: task.id, agent_id: unit?.id || "",
        expected_generation: unit?.observation?.generation || "", command_id });
      state.pending = command_id;
      status.textContent = `Команда принята в очередь: ${receipt.state}. Ожидаю фактический результат.`;
    };
    state.loadPayload = async (task, unit, ref, target) => {
      let offset = 0;
      target.replaceChildren();
      const text = node("pre", "", "forge-output");
      target.append(text);
      const next = button("Прочитать дальше", async () => {
        const page = await state.api("payload", { task_id: task.id, agent_id: unit.id,
          sha256: ref.sha256, kind: ref.kind || "payload", offset });
        text.textContent += page.text;
        offset = page.next_offset;
        next.hidden = offset === null;
      });
      target.append(next);
      next.click();
    };
    state.show = async (id) => {
      state.selected = id;
      const task = await state.api("detail", { task_id: id });
      const summary = node("p", `${task.status} · работает или требует сверки: ${task.counts.active} · лимит агентов ${task.max_active_workers ?? "не задан"} · в очереди ${task.counts.queued} · всего ${task.counts.total}`);
      const controls = node("div", "", "forge-controls");
      for (const [action, label] of [["pause", "Пауза"], ["stop", "Стоп"], ["resume", "Продолжить очередь"]]) {
        controls.append(button(label, () => state.control(task, null, action)));
      }
      const graph = node("details");
      graph.append(node("summary", "План, зависимости и решения координатора"),
        node("pre", JSON.stringify({ criteria: task.success_criteria, plan: task.swarm,
          coordinator: task.coordinator, usage: task.usage }, null, 2), "forge-output"));
      const units = node("div");
      for (const unit of (task.units || task.agents)) {
        const card = node("article", "", "forge-unit");
        card.style.marginInlineStart = `${Math.min(Number(unit.depth || 0), 8) * 12}px`;
        card.append(node("h3", `${unit.role || "участник"} · ${unit.status}`), node("p", unit.subtask),
          node("small", `${unit.id} · родитель ${unit.parent_unit_id || "Праксис"}`));
        const observation = unit.observation || {};
        card.append(node("p", `${observation.phase || "Этап пока не наблюдался"}${observation.tool ? ` · ${observation.tool}` : ""} · ${observation.observed_at || "нет отметки времени"}`),
          node("p", `Результат: ${unit.family && unit.family !== 'agents' ? (unit.result_ref ? 'сохранён' : 'ещё не сохранён') : (unit.current_disposition || "ожидает решения")} · процесс: ${unit.process_liveness} · наблюдение: ${unit.liveness?.observed_at || 'не публиковалось'}`));
        if (unit.root_release_pending) card.append(node('p','Результат сохранён; освобождение рабочего дерева ещё не подтверждено'));
        const commands = node("div", "", "forge-controls");
        for (const [action, label] of (unit.family && unit.family !== 'agents' ? [] : [["pause", "Пауза"], ["stop", "Стоп"], ["resume", "Продолжить"]])) {
          commands.append(button(label, () => state.control(task, unit, action)));
        }
        const output = state.outputs.get(unit.id) || node("div");
        state.outputs.set(unit.id, output);
        if (unit.result_ref) commands.append(button("Полный результат и файлы", () => state.loadPayload(task, unit, unit.result_ref, output)));
        for (const [name, ref] of Object.entries(unit.payload_refs || {})) {
          commands.append(button(name === "diff_ref" ? "Изменения" : name === "files_ref" ? "Файлы" : name,
            () => state.loadPayload(task, unit, ref, output)));
        }
        if (!unit.family || unit.family === 'agents') commands.append(button("Реальные шаги", async () => {
          let offset=0,after_seq=0,generation=observation.generation || '';
          const text=node('pre','','forge-output');output.replaceChildren(text);
          const next=button('Следующая страница шагов',async()=>{
            const page=await state.api('events',{task_id:task.id,agent_id:unit.id,generation,offset,after_seq});
            text.textContent+=page.events.map(row=>JSON.stringify(row)).join('\n')+'\n';
            offset=page.next_offset;after_seq=page.next_after_seq;generation=page.generation;
            next.hidden=!page.has_more;
          });output.append(next);next.click();
        }));
        if (unit.family && unit.family !== 'agents') card.append(node('p','Связанная работа: остановка через владельца ветки или всей команды'),node('pre',JSON.stringify(unit.state,null,2),'forge-output'));
        card.append(commands, output);
        units.append(card);
      }
      const history = await state.api("control_history", { task_id: id });
      const log = node("details");
      log.append(node("summary", "Квитанции управления"), node("pre", JSON.stringify(history.commands, null, 2), "forge-output"));
      detail.replaceChildren(node("h3", task.goal), summary, controls, graph, units, log);
    };
    state.refresh = async (more = false) => {
      if (state.busy || state.stale) return;
      state.busy = true;
      try {
        const page = await state.api("list", { cursor: more ? state.cursor : "" });
        if (!more) list.replaceChildren();
        for (const task of [...page.active, ...page.recent]) {
          list.append(button(`${task.goal || task.id} · ${task.status}`, () => state.show(task.id)));
        }
        state.cursor = page.next_cursor || "";
        if (page.next_cursor) list.append(button("Предыдущие задачи", () => state.refresh(true)));
        if (state.pending) {
          const receipt = await state.api("receipt", { command_id: state.pending });
          status.textContent = `Управление: ${receipt.state}${receipt.reason ? ` · ${receipt.reason}` : ""}`;
          if (!["queued", "processing"].includes(receipt.state)) state.pending = "";
        } else status.textContent = page.active.length ? "Показаны наблюдаемые состояния команды" : "Активных участников сейчас нет";
        if (state.selected && !detail.contains(document.activeElement)) await state.show(state.selected);
        state.last = Date.now();
      } finally { state.busy = false; }
    };
    heading.append(button("Обновить команду", () => state.refresh()));
    mounted.set(host, state);
  }
  state.stale = stale;
  if (!stale && Date.now() - state.last > 5000) state.refresh().catch(error => { state.status.textContent = error.message; });
}
