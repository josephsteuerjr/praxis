import {strict as assert} from 'node:assert';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';

const source = readFileSync(new URL('../../ui-kit/window/main.ts', import.meta.url), 'utf8');
function fragment(from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, `source closure: ${from}`);
  return stripTypeScriptTypes(source.slice(start, end));
}
// Run the actual recording closure and textarea handler with synthetic media.
// No microphone, permission prompt, installed agent or native child process.
const factory = new Function('document', 'navigator', 'MediaRecorder', 'api', 'S',
  'toast', 'addFiles', 'window', 'clearInterval', 'Date', 'doSend', `
  let ownerState = null, engineOperation = null, sending = false;
  const fmtDur = s => String(s), baseMime = s => s.split(';')[0];
  const humanError = e => ({text: String(e)});
  ${fragment('  const micBtn =', '  say.addEventListener("paste"')}
  ${fragment('  function onComposerKeydown(', '  say.addEventListener("keydown", onComposerKeydown)')}
  return {micToggle, syncMicButton, onComposerKeydown, recorder: () => rec,
    setOwner: v => ownerState = v, setSending: v => sending = v,
    setOperation: v => engineOperation = v};
`);
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
function harness(options = {}) {
  const classes = new Set(), attrs = new Map(), timers = new Map(), listeners = new Map();
  const recordings = [], attachments = [], notices = [];
  let mediaCalls = 0, apiCalls = 0, trackStops = 0, sends = 0, clock = 100000;
  const button = options.noButton ? null : {
    disabled: false, title: 'Записать голосовое · Alt+Enter',
    classList: {add: s => classes.add(s), remove: s => classes.delete(s)},
    setAttribute: (k, v) => attrs.set(k, v), addEventListener() {},
  };
  const stream = {getTracks: () => [{stop: () => trackStops++}]};
  const S = {view: 'talk', room: 'window', rooms: [{key: 'window'}]};
  class Recorder {
    static isTypeSupported = () => true;
    constructor(stream, config) {
      if (options.constructorFails) throw Error('constructor failed');
      this.stream = stream; this.mimeType = config?.mimeType || 'audio/webm';
      this.state = 'inactive'; recordings.push(this);
    }
    start() {
      if (options.startFails) throw Error('start failed');
      this.state = 'recording';
    }
    stop() {
      this.state = 'inactive';
      this.ondataavailable?.({data: new Blob(['a'.repeat(2048)])});
      this.onstop?.();
    }
  }
  class Clock extends Date {
    constructor(...args) { super(...(args.length ? args : [clock])); }
    static now = () => clock;
  }
  const document = {querySelector: () => button, addEventListener: (event, fn) => listeners.set(event, fn)};
  const navigator = options.noMedia ? {} : {mediaDevices: {getUserMedia: () => {
    mediaCalls++;
    return options.media ? options.media.promise : Promise.resolve(stream);
  }}};
  const api = () => { apiCalls++; return options.api ? options.api.promise : Promise.resolve({ready: true}); };
  const window = {setInterval: fn => { const id = timers.size + 1; timers.set(id, fn); return id; }};
  const h = factory(document, navigator, options.noRecorder ? undefined : Recorder, api, S,
    v => notices.push(v), (files, seconds) => attachments.push({files, seconds}),
    window, id => timers.delete(id), Clock, () => sends++);
  h.syncMicButton();
  return {...h, S, button, attrs, classes, timers, recordings, attachments, notices, options,
    stream, setClock: v => clock = v,
    counts: () => ({mediaCalls, apiCalls, trackStops, sends}),
    key: changes => {
      const event = {key: 'Enter', altKey: true, ctrlKey: false, metaKey: false,
        shiftKey: false, isComposing: false, repeat: false, defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; }, ...changes};
      // Bubble order: textarea first, then document's real registered handler.
      h.onComposerKeydown(event);
      listeners.get('keydown')(event);
      return event;
    },
  };
}

{
  const h = harness();
  assert.equal(h.key().defaultPrevented, true);
  await flush();
  assert.equal(h.recordings.length, 1);
  assert.equal(h.recorder().state, 'recording');
  assert.equal(h.button.disabled, false);
  assert.ok(h.classes.has('recording'));
  assert.match(h.button.title, /Alt\+Enter/);
  h.key(); await flush();
  assert.equal(h.attachments.length, 1, 'second press finishes one attachment');
  const file = h.attachments[0].files[0];
  assert.equal(file.size, 2048); assert.equal(file.type, 'audio/webm');
  assert.match(file.name, /^voice-.*\.webm$/);
  assert.equal(h.counts().trackStops, 1);
  assert.equal(h.counts().sends, 0, 'voice shortcut never sends the draft');
  assert.equal(h.timers.size, 0); assert.equal(h.recorder(), null);
  assert.match(h.button.title, /Alt\+Enter/);
  h.key({altKey: false});
  assert.equal(h.counts().sends, 1, 'ordinary Enter still sends');
  h.key({altKey: false, shiftKey: true});
  h.key({altKey: false, isComposing: true});
  h.setSending(true); h.key({altKey: false});
  assert.equal(h.counts().sends, 1, 'newline, IME and in-flight send are preserved');
}
for (const change of [{repeat: true}, {isComposing: true}, {ctrlKey: true},
  {metaKey: true}, {shiftKey: true}, {defaultPrevented: true}, {key: 'Escape'}]) {
  const h = harness(); h.key(change); await flush();
  assert.equal(h.counts().apiCalls, 0, JSON.stringify(change));
  assert.equal(h.counts().sends, 0);
}
{
  const h = harness(); h.key(); await flush();
  assert.equal(h.key({repeat: true}).defaultPrevented, true);
  assert.equal(h.recorder().state, 'recording', 'holding the keys cannot stop recording');
  await h.micToggle();
}
for (const configure of [h => h.S.view = 'settings', h => h.S.rooms[0].stub = true,
  h => h.setOwner({stopped: true}), h => h.setOperation({kind: 'restart'})]) {
  const h = harness(); configure(h); h.syncMicButton(); h.key(); await h.micToggle();
  assert.equal(h.counts().apiCalls, 0, 'unavailable composer cannot start recording');
}
{
  const h = harness({noButton: true});
  h.key(); await h.micToggle(); h.syncMicButton();
  assert.equal(h.counts().apiCalls, 0, 'remote edition without a mic remains safe');
}
{
  const api = deferred(), media = deferred(), h = harness({api, media});
  h.key(); h.key(); h.key({repeat: true});
  await h.micToggle();
  assert.equal(h.counts().apiCalls, 1, 'all entry points share pending start');
  assert.equal(h.button.disabled, true);
  api.resolve({ready: true}); await flush();
  h.key(); await h.micToggle();
  assert.equal(h.counts().mediaCalls, 1, 'only one pending media permission');
  media.resolve(h.stream); await flush();
  assert.equal(h.recordings.length, 1); assert.equal(h.button.disabled, false);
  h.setOwner({stopped: true}); h.syncMicButton();
  assert.equal(h.button.disabled, false, 'engine stop does not trap the microphone');
  h.key(); await flush();
  assert.equal(h.counts().trackStops, 1); assert.equal(h.attachments.length, 1);
  assert.equal(h.button.disabled, true);
}
for (const change of [h => h.S.room = 'other', h => h.S.view = 'settings', h => h.setOwner({stopped: true})]) {
  const media = deferred(), h = harness({media});
  h.key(); await flush(); change(h);
  media.resolve(h.stream); await flush();
  assert.equal(h.recordings.length, 0, 'scope changes during permission cancel the pending start');
  assert.equal(h.counts().trackStops, 1, 'cancelled permission stream is released');
  assert.equal(h.timers.size, 0); assert.equal(h.attachments.length, 0);
}
{
  const api = deferred(), h = harness({api});
  h.key(); h.S.room = 'other'; api.resolve({ready: true}); await flush();
  assert.equal(h.counts().mediaCalls, 0, 'scope rechecked before requesting media');
}
for (const options of [{noMedia: true}, {noRecorder: true}, {constructorFails: true}, {startFails: true}]) {
  const h = harness(options); await h.micToggle();
  assert.equal(h.recorder(), null); assert.equal(h.button.disabled, false);
  assert.equal(h.timers.size, 0); assert.equal(h.attachments.length, 0);
  assert.equal(h.counts().trackStops, options.constructorFails || options.startFails ? 1 : 0);
}
{
  const media = deferred(), h = harness({media});
  h.key(); await flush(); media.reject(Error('permission denied')); await flush();
  assert.equal(h.button.disabled, false); assert.equal(h.recorder(), null);
  assert.equal(h.attachments.length, 0); assert.match(h.notices[0], /Микрофон не дали/);
}
{
  const api = deferred(), h = harness({api});
  h.key(); api.resolve({ready: false, why: 'not ready'}); await flush();
  assert.equal(h.counts().mediaCalls, 0); assert.equal(h.button.disabled, false);
}
{
  const h = harness(); await h.micToggle(); const old = h.recorder();
  old.onerror(); assert.equal(h.counts().trackStops, 1);
  await h.micToggle(); const current = h.recorder();
  old.ondataavailable({data: new Blob(['b'.repeat(2048)])}); old.onstop(); old.onerror();
  assert.equal(h.recorder(), current, 'late failed recorder events cannot stop a new capture');
  assert.equal(h.attachments.length, 0);
  await h.micToggle(); assert.equal(h.attachments[0].files[0].size, 2048);
}
{
  const h = harness(); await h.micToggle(); h.setClock(400000);
  [...h.timers.values()][0]();
  assert.equal(h.recorder(), null); assert.equal(h.counts().trackStops, 1);
  assert.equal(h.attachments[0].seconds, 300, 'five-minute limit still finishes recording');
}
console.log('voice shortcut: bubble order, repeat/IME, single-flight permission, cleanup and remote edition OK');
