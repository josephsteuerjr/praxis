import { t as uiText, locale } from "../../lang/lang";
import { shell } from './api';
import { button, card, el } from '../dom';

type Progress = { active?: boolean; state?: string; stage?: string; bytes?: number; total_bytes?: number; files?: number; files_done?: number; total_files?: number; error?: string };
type Component = { id: string; ready: boolean; installed: boolean; restart_required?:boolean; error?: string; source?: string; toolchain?: string; download_bytes?: number; progress?: Progress; runtime?: { state: string; running: boolean; tools: number; error?: string } };

export function componentProgress(value: Progress): { label: string; fraction?: number } {
  const labels: Record<string, string> = { starting: uiText("ui.ui.kit.window.components.card.5ea5c68f20"), preparing: uiText("ui.ui.kit.window.components.card.1484c59287"), downloading: uiText("ui.ui.kit.window.components.card.71720d31eb"), unpacking: uiText("ui.ui.kit.window.components.card.cb9e525ef6"), extracting: uiText("ui.ui.kit.window.components.card.cb9e525ef6"), installing: uiText("ui.ui.kit.window.components.card.a95642d636"), completed: uiText("ui.ui.kit.window.components.card.0b29a3640f") };
  let label = labels[value.stage || ''] || uiText("ui.ui.kit.window.components.card.a95642d636");
  let done: number | undefined, total: number | undefined;
  if (value.stage === 'downloading' && value.total_bytes) {
    done = value.bytes || 0; total = value.total_bytes;
    label += uiText("ui.ui.kit.window.components.card.8686d78f52", {p0: (done / 1048576).toLocaleString(locale(), {minimumFractionDigits: 1, maximumFractionDigits: 1}), p1: (total / 1048576).toLocaleString(locale(), {minimumFractionDigits: 1, maximumFractionDigits: 1})});
  } else if (value.total_files) {
    done = value.files_done ?? value.files ?? 0; total = value.total_files;
    label += uiText("ui.ui.kit.window.components.card.09c17de177", {p0: String(done), p1: String(total)});
  }
  return { label, fraction: total && done !== undefined ? Math.max(0, Math.min(1, done / total)) : undefined };
}

/** The startup view and Settings use the same component controls. */
export function componentsCard(): HTMLElement {
  const body = el('div');
  const note = el('p', 'field-hint', uiText("ui.ui.kit.window.components.card.132814adda"));
  const rows = el('div');
  const receipt = el('p', 'receipt');
  const entries = new Map<string, { row: HTMLElement; label: HTMLElement; detail: HTMLElement; bar: HTMLProgressElement; install: HTMLButtonElement; error: HTMLElement }>();
  const starting = new Set<string>();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const schedule = (delay = 1000) => {
    clearTimeout(timer);
    timer = setTimeout(() => { if (body.isConnected) void render(); }, delay);
  };
  const render = async () => {
    try {
      const components = await shell<Component[]>('components_status');
      for (const item of components) {
        let entry = entries.get(item.id);
        if (!entry) {
          const row = el('div', 'component-install');
          const label = el('span'), detail = el('p', 'field-hint'), bar = el('progress');
          bar.max = 1;
          const error = el('p', 'receipt err');
          const install = button('', 'quiet', async () => {
            starting.add(item.id); install.disabled = true; detail.textContent = uiText("ui.ui.kit.window.components.card.5ea5c68f20"); receipt.textContent = '';
            try {
              const installed = entries.get(item.id)?.install.dataset.installed === 'true';
              const result = await shell<Progress>(item.id === 'windows-mcp' && installed ? 'windows_mcp_start' : 'component_install', { name: item.id });
              receipt.textContent = item.id === 'windows-mcp' && installed ? uiText("ui.ui.kit.window.components.card.4ce98940fb") : result?.state === 'running' ? uiText("ui.ui.kit.window.components.card.14c485e361") : uiText("ui.ui.kit.window.components.card.02c5127a3c");
            } catch (e) { receipt.textContent = String(e); }
            finally { starting.delete(item.id); await render(); }
          });
          row.append(label, detail, bar, install, error); rows.append(row);
          entry = { row, label, detail, bar, install, error }; entries.set(item.id, entry);
        }
        const names: Record<string, string> = {relay:'praxis-relay · MIT', xtts:uiText("ui.ui.kit.window.components.card.e594cd8ea7"), whisper:uiText("ui.ui.kit.window.components.card.f56e4834f2"),'server-bridge':uiText("ui.ui.kit.window.components.card.719d7aa17f"),'windows-mcp':'Windows MCP · MIT'};
        const active = starting.has(item.id) || item.progress?.active === true;
        const caption = componentProgress(item.progress || {});
        entry.label.textContent = `${names[item.id] || item.id} · ${active ? uiText("ui.ui.kit.window.components.card.5eabb2b659") : item.installed ? uiText("ui.ui.kit.window.components.card.4d9b985d9a") : item.ready ? uiText("ui.ui.kit.window.components.card.8e58beddaf") : uiText("ui.ui.kit.window.components.card.5023a2c800")}`;
        entry.detail.textContent = active ? caption.label : item.installed ? item.restart_required===false ? uiText("ui.ui.kit.window.components.card.88c88bbffe") : uiText("ui.ui.kit.window.components.card.0c90a5573f") : item.download_bytes ? uiText("ui.ui.kit.window.components.card.08031027fe", {p0: String((item.download_bytes / 1048576).toFixed(0))}) : '';
        if (item.id === 'windows-mcp' && item.installed && !active) {
          entry.detail.textContent = item.runtime?.running ? uiText("ui.ui.kit.window.components.card.db6f731e2a", {p0: String(item.runtime.tools)}) : item.runtime?.state === 'starting' ? uiText("ui.ui.kit.window.components.card.c975c05108") : uiText("ui.ui.kit.window.components.card.b6164e8848");
        }
        entry.bar.hidden = !active;
        if (caption.fraction === undefined) entry.bar.removeAttribute('value'); else entry.bar.value = caption.fraction;
        entry.install.dataset.installed = String(item.installed);
        entry.install.hidden = !item.ready || (item.installed && (item.id !== 'windows-mcp' || item.runtime?.running === true));
        entry.install.disabled = active || item.progress?.state === 'attention' || item.runtime?.state === 'starting';
        entry.install.textContent = active ? uiText("ui.ui.kit.window.components.card.227a1109b5") : item.id === 'windows-mcp' && item.installed ? uiText("ui.ui.kit.window.components.card.c9d2fdd162") : item.progress?.error ? uiText("ui.ui.kit.window.components.card.5189135a61") : item.id === 'relay' ? uiText("ui.ui.kit.window.components.card.b7cd6fae08") : uiText("ui.ui.kit.window.components.card.be83518bd3");
        entry.error.textContent = item.error || item.progress?.error || item.runtime?.error || '';
      }
      schedule(components.some(item => item.progress?.active || item.runtime?.state === 'starting') || starting.size ? 1000 : 5000);
    } catch (e) { receipt.textContent = String(e); }
  };
  body.append(note, rows, receipt);
  void render();
  return card(uiText("ui.ui.kit.window.components.card.9077985832"), body);
}
