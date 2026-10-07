import test from 'node:test';
import assert from 'node:assert/strict';
import { placeTray } from '../../ui-kit/tray-position.ts';
import { trayStatus, exitNote, engineCommand } from '../../ui-kit/tray-state.ts';

test('paper remains inside the work area at every edge, including negative monitor origins', () => {
  for (const area of [{ x: 0, y: 0, width: 1920, height: 1040 }, { x: -1280, y: -720, width: 1280, height: 680 }, { x: 1920, y: 200, width: 640, height: 300 }]) {
    for (const x of [area.x - 10, area.x + area.width / 2, area.x + area.width + 10]) {
      for (const y of [area.y - 10, area.y + area.height + 10]) {
        const box = placeTray({ x, y }, area, 560);
        assert.ok(box.x + 24 >= area.x + 8);
        assert.ok(box.y + 24 >= area.y + 8);
        assert.ok(box.x + box.width - 24 <= area.x + area.width - 8);
        assert.ok(box.y + box.height - 24 <= area.y + area.height - 8);
      }
    }
  }
});
test('top and bottom trays place the card on the usable side', () => {
  const area = { x: 0, y: 28, width: 1440, height: 872 };
  const top = placeTray({ x: 1400, y: 14 }, area, 350);
  const bottom = placeTray({ x: 1400, y: 930 }, area, 350);
  assert.equal(top.y + 24, 36);
  assert.ok(bottom.y + bottom.height - 24 <= 892);
  assert.throws(() => placeTray({ x: NaN, y: 0 }, area, 300));
  assert.throws(() => placeTray({ x: 0, y: 0 }, { ...area, width: 0 }, 300));
});
test('installed background work does not imply a running engine', () => {
  assert.equal(trayStatus({ runner_alive: false }).text, 'Не запущен');
  assert.equal(trayStatus({ runner_alive: null }).text, 'Состояние уточняется');
  assert.equal(trayStatus({ runner_alive: true, stopped: true }).text, 'Остановлен');
  assert.equal(trayStatus({ runner_alive: true, enabled: false }).text, 'Остановлен');
  assert.match(exitNote({ background: true }), /Фоновая работа останется/);
  assert.match(exitNote({ background: false }), /остановит/);
  assert.match(exitNote({ background: null }), /неизвестно/);
});
test('engine action respects the selected agent and the owner stop', () => {
  assert.deepEqual(engineCommand({ supported: true, stopped: true, runner_alive: false }), { cmd: 'owner_control', args: { action: 'resume' } });
  assert.deepEqual(engineCommand({ supported: true, stopped: false, runner_alive: false }), { cmd: 'engine_restart', args: {} });
  assert.deepEqual(engineCommand({ supported: true, runner_alive: true }), { cmd: 'owner_control', args: { action: 'panic' } });
  assert.deepEqual(engineCommand({ supported: true, service: true, enabled: false, agent_id: 'second' }), { cmd: 'agent_enabled_set', args: { id: 'second', enabled: true } });
  assert.equal(engineCommand({ supported: false }), null);
});
