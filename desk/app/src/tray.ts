import '../../ui-kit/fonts.css';
import '../../ui-kit/tokens.css';
import '../../ui-kit/window/styles/paper-tokens.css';
import './tray.css';
import { shell } from '../../ui-kit/window/api';
import { apply, load } from '../../ui-kit/window/look';
import { engineCommand, exitNote, trayStatus, type TrayState } from '../../ui-kit/tray-state';

const root = document.querySelector<HTMLElement>('#tray')!;
let state: TrayState | null = null;
let pending = false;
let timer: number | undefined;
let error = '';
let generation = 0;
const element = <K extends keyof HTMLElementTagNameMap>(tag: K, cls: string, text = '') => {
  const node = document.createElement(tag); node.className = cls; node.textContent = text; return node;
};
const hide = () => shell('tray_hide').catch(() => {});

async function action(run: () => Promise<unknown>, close = true) {
  if (pending) return;
  pending = true; error = ''; paint();
  try { await run(); if (close) await hide(); }
  catch (e) {
    error = e instanceof Error ? e.message : String(e); paint();
    if (!document.hasFocus()) void shell('notify', { title: 'Hélène', body: error }).catch(() => {});
  } finally { pending = false; if (!close && !error) await refresh(); paint(); }
}

function button(label: string, run: () => Promise<unknown>, close = true) {
  const node = element('button', 'tray-action', label); node.type = 'button'; node.disabled = pending;
  node.addEventListener('click', () => void action(run, close)); return node;
}

function paint() {
  const focused = document.activeElement instanceof HTMLElement ? document.activeElement.dataset.action : undefined;
  root.replaceChildren();
  root.style.maxHeight = state?.max_height ? Math.max(160, state.max_height - 48) + 'px' : '';
  root.append(element('div', 'tray-brand', 'Hélène'));
  if (!state) {
    root.append(element('p', 'tray-status', error || 'Уточняю состояние…'));
  } else {
    const current = state.agents.find(a => a.id === state!.current);
    const name = element('h1', 'tray-name', current?.name || 'Агент'); name.title = name.textContent || '';
    root.append(name);
    const status = trayStatus(state.owner);
    const row = element('div', 'tray-status');
    row.append(element('span', 'tray-dot ' + status.tone), element('span', '', pending ? 'Применяю…' : status.text));
    root.append(row);
    if (state.agents.length > 1) {
      const agents = element('div', 'tray-agents'); agents.setAttribute('aria-label', 'Выбрать агента');
      for (const a of state.agents) {
        const pick = button(a.name, () => shell('switch_agent', { id: a.id }));
        pick.dataset.action = 'agent:' + a.id;
        pick.classList.add('tray-agent'); pick.classList.toggle('current', a.id === state.current);
        pick.setAttribute('aria-pressed', String(a.id === state.current)); pick.title = a.conflict || a.name;
        pick.disabled ||= !!a.conflict; agents.append(pick);
      }
      root.append(agents);
    }
  }
  const open = button('Открыть Элен', () => shell('tray_open_main')); open.dataset.action = 'open'; root.append(open);
  if (state) {
    const cmd = engineCommand(state.owner);
    const stopped = !!state.owner.stopped || state.owner.enabled === false || state.owner.runner_alive === false;
    const control = button(stopped ? 'Запустить движок' : 'Остановить движок', () => shell(cmd!.cmd, cmd!.args), false);
    control.dataset.action = 'engine'; control.disabled ||= !cmd;
    if (!cmd) control.title = 'Управление движком на этой платформе недоступно';
    root.append(control);
  }
  if (error) { const msg = element('p', 'tray-error', error); msg.setAttribute('role', 'alert'); root.append(msg); }
  const footer = element('div', 'tray-footer');
  const quit = button('Выйти из Элен', () => shell('tray_exit')); quit.dataset.action = 'exit'; footer.append(quit);
  if (state) footer.append(element('p', 'tray-note', exitNote(state)));
  root.append(footer);
  if (focused) root.querySelector<HTMLElement>(`[data-action="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
  if (document.hasFocus()) void fit();
}

async function fit() {
  await document.fonts.ready;
  const height = Math.ceil(root.getBoundingClientRect().height + 48);
  await shell('tray_fit', { height }).catch(() => {});
}

async function refresh() {
  if (pending) return;
  const stamp = ++generation;
  try {
    const fresh = await shell<TrayState>('tray_context');
    if (stamp !== generation) return;
    state = fresh; paint();
  } catch (e) {
    if (stamp !== generation) return;
    error = e instanceof Error ? e.message : String(e); paint();
  }
}

function opened() {
  apply(load()); void refresh();
  clearInterval(timer); timer = window.setInterval(() => void refresh(), 1500);
  requestAnimationFrame(() => root.querySelector<HTMLButtonElement>('[data-action="open"]')?.focus());
}
window.addEventListener('focus', opened);
window.addEventListener('blur', () => { clearInterval(timer); ++generation; void hide(); });
window.addEventListener('storage', () => apply(load()));
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { e.preventDefault(); void hide(); return; }
  const buttons = [...root.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
  if (!buttons.length) return;
  const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
  let next: number | undefined;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') next = (index + (e.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
  else if (e.key === 'Home') next = 0;
  else if (e.key === 'End') next = buttons.length - 1;
  else if (e.key === 'Tab') next = (index + (e.shiftKey ? -1 : 1) + buttons.length) % buttons.length;
  if (next !== undefined) { e.preventDefault(); buttons[next].focus({ preventScroll: true }); }
});
apply(load()); paint();
if (document.hasFocus()) opened();
