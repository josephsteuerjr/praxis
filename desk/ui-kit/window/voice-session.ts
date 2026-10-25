/** One recording, pinned recipient, explicit pause/send/cancel. No keyboard or DOM ownership. */
export type VoiceState = 'idle' | 'starting' | 'recording' | 'paused' | 'sending';
export interface VoiceTarget { agent: string; room: string; name: string; route?: string }
export interface VoiceDependencies {
  media(): Promise<MediaStream>;
  recorder(stream: MediaStream): MediaRecorder;
  send(blob: Blob, target: VoiceTarget): Promise<void>;
  changed(session: VoiceSession): void;
  error(message: string): void;
  now?(): number;
}
export class VoiceSession {
  state: VoiceState = 'idle';
  target: VoiceTarget | null = null;
  stream: MediaStream | null = null;
  recorder: MediaRecorder | null = null;
  blob: Blob | null = null;
  private chunks: Blob[] = [];
  private epoch = 0;
  private elapsed = 0;
  private since = 0;
  private parked = false;
  private mode: 'pause' | 'send' = 'pause';
  private flushes = new Set<() => void>();
  constructor(private deps: VoiceDependencies) {}
  private now() { return this.deps.now?.() ?? Date.now(); }
  get seconds() { return (this.elapsed + (this.state === 'recording' ? this.now() - this.since : 0)) / 1000; }
  private changed() { this.deps.changed(this); }
  async hold(target: VoiceTarget, mode: 'pause' | 'send') {
    if (this.state === 'sending' || this.state === 'starting' || this.state === 'recording') return;
    if (this.target && (this.target.agent !== target.agent || this.target.room !== target.room || this.target.route !== target.route)) {
      this.deps.error('Сначала отправь или удали запись для «' + this.target.name + '».'); return;
    }
    this.parked = false; this.mode = mode;
    if (this.recorder?.state === 'paused') {
      this.stream?.getAudioTracks().forEach(track => { track.enabled = true; });
      this.recorder.resume(); this.since = this.now(); this.state = 'recording'; this.changed(); return;
    }
    const epoch = ++this.epoch;
    this.target = {...target}; this.state = 'starting'; this.changed();
    let stream: MediaStream | null = null;
    try {
      stream = await this.deps.media();
      // Releasing/cancelling while the OS permission dialog is open must not
      // start a surprise recording after consent eventually arrives.
      if (epoch !== this.epoch) { stream.getTracks().forEach(t => t.stop()); return; }
      this.stream = stream;
      const recorder = this.deps.recorder(stream); this.recorder = recorder;
      recorder.ondataavailable = event => {
        if (epoch !== this.epoch) return;
        if (event.data.size) this.chunks.push(event.data);
        this.blob = new Blob(this.chunks, {type: recorder.mimeType || 'audio/webm'});
        for (const resolve of this.flushes) resolve(); this.flushes.clear();
        this.changed();
      };
      recorder.onerror = () => { if (epoch === this.epoch) { this.cancel(); this.deps.error('Микрофон отключился. Запиши сообщение заново.'); } };
      recorder.start(250); this.since = this.now(); this.state = 'recording'; this.changed();
    } catch (error) {
      stream?.getTracks().forEach(t => t.stop());
      if (epoch === this.epoch) { this.cancel(); this.deps.error('Не удалось включить микрофон: ' + String(error)); }
    }
  }
  release() {
    if (this.state === 'starting') { this.cancel(); return; }
    if (this.parked || this.state !== 'recording') return;
    if (this.mode === 'send') void this.send(); else this.pause();
  }
  pause() {
    this.parked = true;
    if (this.state === 'starting') { this.cancel(); return; }
    if (this.state !== 'recording' || !this.recorder) return;
    this.elapsed += this.now() - this.since;
    this.recorder.pause(); this.state = 'paused';
    this.stream?.getAudioTracks().forEach(track => { track.enabled = false; });
    this.recorder.requestData(); this.changed();
  }
  async send() {
    if (this.state !== 'recording' && this.state !== 'paused') return;
    this.pause();
    if (!this.recorder || !this.target) return;
    const epoch = this.epoch, target = {...this.target};
    this.state = 'sending'; this.changed();
    try {
      await new Promise<void>((resolve, reject) => {
        const finish = () => { clearTimeout(timer); this.flushes.delete(finish); resolve(); };
        const timer = setTimeout(() => { this.flushes.delete(finish); reject(Error('Запись ещё не готова. Повтори отправку.')); }, 5000);
        this.flushes.add(finish);
        this.recorder!.requestData();
      });
      if (epoch !== this.epoch) return;
      if (!this.blob || this.blob.size < 128 || this.seconds < .25) throw Error('Запись слишком короткая. Продолжи её.');
      await this.deps.send(this.blob, target);
      if (epoch === this.epoch) this.reset();
    } catch (error) {
      if (epoch === this.epoch) { this.state = 'paused'; this.deps.error(String(error)); this.changed(); }
    }
  }
  cancel() { if (this.state !== 'sending') this.reset(); }
  dispose() { this.reset(); }
  private reset() {
    ++this.epoch;
    const recorder = this.recorder; this.recorder = null;
    if (recorder && recorder.state !== 'inactive') recorder.stop();
    this.stream?.getTracks().forEach(t => t.stop()); this.stream = null;
    for (const resolve of this.flushes) resolve(); this.flushes.clear();
    this.state = 'idle'; this.target = null; this.blob = null; this.chunks = [];
    this.elapsed = 0; this.parked = false; this.changed();
  }
}
