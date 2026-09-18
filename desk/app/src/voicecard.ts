// Карточка «Голос»: слышит ли агент голосовые на этом ПК.
//
// Что здесь особенного и почему это отдельный файл. Голос — единственная часть
// продукта, которой НЕ ХВАТАЕТ ГИГАБАЙТА, чтобы работать: библиотека едет в
// рантайме, а модель — нет (самая маленькая 480 МБ, рабочая 1,6 ГБ). Значит
// карточка обязана уметь то, чего не умеет ни одна другая: показать, чего не
// хватает, скачать это и рассказывать о ходе дела, не притворяясь готовой.
//
// Состояние спрашивается у КАНАЛА (`/api/voice`, тот же `localharness/voice.py`,
// которым голос поднимает раннер), а не считается здесь: вторая правда о том,
// скачана ли модель, разъехалась бы с первой молча.
import { api, shell } from "../../ui-kit/window/api";
import { el, humanError } from "../../ui-kit/window/lib";
import { button, card, choice, toggle } from "../../ui-kit/dom";
import type { Config } from "../../ui-kit/window/views/settings-frame";

interface VoiceModel {
  id: string;
  title: string;
  note: string;
  size_mb: number;
  installed: boolean;
}

interface VoiceState {
  enabled: boolean;
  model: string;
  ready: boolean;
  why: string;
  dir: string;
  library: { present: boolean; why: string };
  installed: { model?: string; path?: string; bytes?: number };
  catalog: VoiceModel[];
  speech?: SpeechState;
  download: {
    state: string;
    model: string;
    note: string;
    got_bytes: number;
    total_bytes: number;
    updated_utc: string;
  } | null;
}

/** Голос агента наружу — вторая половина того же ответа канала. */
interface SpeechState {
  enabled: boolean;
  voice: string;
  ready: boolean;
  why: string;
  dir: string;
  model: string;
  library: { present: boolean; why: string };
  catalog: Array<{ id: string; title: string; note: string; size_mb: number; installed: boolean }>;
  download: { state: string; voice: string; done_mb?: number; size_mb?: number; at?: string; error?: string } | null;
}

export interface VoiceCard {
  el: HTMLElement;
  enabled(): boolean;
  model(): string;
  keepLoaded(): boolean;
  speak(): boolean;
  speakVoice(): string;
}

const MB = 1024 * 1024;

function mb(bytes: number | undefined): string {
  return bytes ? `${Math.round(bytes / MB)} МБ` : "0 МБ";
}

/** Карточка голоса. Черновик правится на месте, как у соседей. */
export function voiceCard(draft: Config): VoiceCard {
  const voice = (draft.voice = draft.voice || {});
  // Умолчание — ВЫКЛЮЧЕНО: без скачанной модели включённый голос был бы
  // обещанием, которое нечем исполнить.
  let enabled = !!voice.enabled;
  let model = String(voice.model || "turbo");
  let keep = !!voice.keep_loaded;
  // Речь — отдельный выключатель, а не часть слуха: слышать голосовые и
  // отвечать голосом — разные желания, и одно без другого законно.
  let speak = !!voice.speak;
  let speakVoice = String(voice.voice || "irina");

  const box = el("div");
  const status = el("p", "field-hint", "спрашиваю канал…");
  const pickBox = el("div");
  const actions = el("div", "actions");
  const note = el("span", "receipt");
  const fetchBtn = button("Скачать модель", "quiet", () => void start());
  actions.append(fetchBtn, note);

  const onOff = toggle("Расшифровывать голосовые на этом ПК", enabled, (v) => {
    enabled = v;
    voice.enabled = v;
    void refresh();
  });
  const keepOff = toggle("Держать модель в памяти между голосовыми", keep, (v) => {
    keep = v;
    voice.keep_loaded = v;
  });

  let picker = choice<string>([], model, () => {});
  let timer: ReturnType<typeof setTimeout> | null = null;

  const drawPicker = (state: VoiceState) => {
    const items = state.catalog.map((m) => ({
      value: m.id,
      title: m.title + (m.installed ? " · скачана" : ` · ${m.size_mb} МБ`),
      text: m.note,
    }));
    picker = choice<string>(items, model, (v) => {
      model = v;
      voice.model = v;
      void refresh();
    }, { stack: true });
    pickBox.replaceChildren(picker.el);
  };

  const say = (state: VoiceState) => {
    const down = state.download;
    const busy = !!down && down.state === "running" && fresh(down.updated_utc);
    fetchBtn.disabled = busy;
    if (busy) {
      const total = down!.total_bytes || 1;
      note.className = "receipt";
      note.textContent = `качаю ${down!.model}: ${mb(down!.got_bytes)} из ~${mb(total)}`;
    } else if (down && down.state === "failed") {
      note.className = "receipt err";
      note.textContent = down.note;
    } else if (down && down.state === "running") {
      // Помощник качал и пропал: окно закрыли, машину усыпили, процесс убит.
      // Вечное «качаю…» здесь было бы враньём, поэтому запись читается со
      // сроком годности.
      note.className = "receipt err";
      note.textContent = "скачивание оборвалось — нажми ещё раз";
    } else if (down && down.state === "done" && state.installed.path) {
      note.className = "receipt ok";
      note.textContent = `модель на месте, ${mb(state.installed.bytes)}`;
    }
    if (!state.library.present) {
      status.textContent = state.library.why + ". Голос в этой сборке не поднимется.";
      fetchBtn.disabled = true;
      return;
    }
    const where = `Модели лежат в ${state.dir}.`;
    if (state.ready) {
      status.textContent =
        `Агент слышит: модель ${state.installed.model || "?"} (${mb(state.installed.bytes)}) на диске. ` +
        (state.why ? state.why + ". " : "") +
        `Расшифровка идёт на процессоре, примерно за половину длительности записи. ${where}`;
    } else {
      status.textContent = `Агент не слышит: ${state.why}. ${where}`;
    }
  };

  const fresh = (stamp: string): boolean => {
    const at = Date.parse(stamp || "");
    return Number.isFinite(at) && Date.now() - at < 60_000;
  };

  const refresh = async () => {
    try {
      const state = await api<VoiceState>("/api/voice");
      // Выбор владельца в черновике главнее того, что лежит в файле: он мог
      // переключить модель и ещё не нажать «Сохранить».
      const shown = { ...state, model, enabled };
      if (!picker.el.childElementCount) drawPicker(shown);
      say(shown);
      // Речь приезжает тем же ответом канала — второй запрос разъезжался бы с
      // первым ровно тогда, когда владелец нажимает обе кнопки подряд.
      const speech = state.speech;
      if (speech) {
        const mine = { ...speech, voice: speakVoice, enabled: speak };
        if (!speechPicker.el.childElementCount) drawVoices(mine);
        sayVoice(mine);
        const dv = speech.download;
        if (dv && dv.state === "running" && fresh(dv.at || "")) {
          timer = setTimeout(() => void refresh(), 2000);
        }
      } else {
        speechStatus.textContent = "этот канал про голос агента ничего не знает";
        speechBtn.disabled = true;
      }
      const down = state.download;
      if (down && down.state === "running" && fresh(down.updated_utc)) {
        timer = setTimeout(() => void refresh(), 2000);
      } else if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    } catch (e) {
      status.textContent = "канал не ответил про голос: " + humanError(e).text;
    }
  };

  const start = async () => {
    note.className = "receipt";
    note.textContent = "запускаю помощника…";
    fetchBtn.disabled = true;
    try {
      const said = await shell<string>("voice_fetch", { model });
      note.textContent = said;
      setTimeout(() => void refresh(), 1500);
    } catch (e) {
      note.className = "receipt err";
      note.textContent = humanError(e).text;
      fetchBtn.disabled = false;
    }
  };

  // ---- вторая половина: агент говорит сам -----------------------------------
  const speechStatus = el("p", "field-hint", "спрашиваю канал…");
  const speechPickBox = el("div");
  const speechActions = el("div", "actions");
  const speechNote = el("span", "receipt");
  const speechBtn = button("Скачать голос", "quiet", () => void startVoice());
  speechActions.append(speechBtn, speechNote);
  const speakOff = toggle("Отвечать голосом, когда сочтёт нужным", speak, (v) => {
    speak = v;
    voice.speak = v;
    void refresh();
  });
  let speechPicker = choice<string>([], speakVoice, () => {});

  const drawVoices = (state: SpeechState) => {
    const items = state.catalog.map((one) => ({
      value: one.id,
      title: one.title + (one.installed ? " · скачан" : ` · ${one.size_mb} МБ`),
      text: one.note,
    }));
    speechPicker = choice<string>(items, speakVoice, (v) => {
      speakVoice = v;
      voice.voice = v;
      void refresh();
    }, { stack: true });
    speechPickBox.replaceChildren(speechPicker.el);
  };

  const sayVoice = (state: SpeechState) => {
    const down = state.download;
    const busy = !!down && down.state === "running" && fresh(down.at || "");
    speechBtn.disabled = busy;
    if (busy) {
      speechNote.className = "receipt";
      speechNote.textContent = `качаю ${down!.voice}: ${down!.done_mb ?? 0} из ~${down!.size_mb ?? "?"} МБ`;
    } else if (down && down.state === "failed") {
      speechNote.className = "receipt err";
      speechNote.textContent = String(down.error || "не скачалось");
    } else if (down && down.state === "running") {
      speechNote.className = "receipt err";
      speechNote.textContent = "скачивание оборвалось — нажми ещё раз";
    } else if (down && down.state === "done") {
      speechNote.className = "receipt ok";
      speechNote.textContent = "голос на месте";
    }
    if (!state.library.present) {
      speechStatus.textContent = state.library.why + ". Говорить в этой сборке нечем.";
      speechBtn.disabled = true;
      return;
    }
    speechStatus.textContent = state.ready
      ? `Агент может говорить: голос ${state.voice} на диске. Синтез идёт на процессоре — ` +
        `первая фраза около трёх секунд (голос читается с диска), дальше доли секунды. Голоса лежат в ${state.dir}.`
      : `Агент отвечает текстом: ${state.why}. Голоса лежат в ${state.dir}.`;
  };

  const startVoice = async () => {
    speechNote.className = "receipt";
    speechNote.textContent = "запускаю помощника…";
    speechBtn.disabled = true;
    try {
      const said = await shell<string>("voice_fetch", { model: speakVoice, kind: "speak" });
      speechNote.textContent = said;
      setTimeout(() => void refresh(), 1500);
    } catch (e) {
      speechNote.className = "receipt err";
      speechNote.textContent = humanError(e).text;
      speechBtn.disabled = false;
    }
  };

  box.append(onOff, status, pickBox, actions, keepOff,
    el("p", "field-hint",
      "Модель держится в памяти около 1,5 ГБ. Дома это дорого за несколько голосовых в день, поэтому по умолчанию " +
      "она загружается на время расшифровки и отпускается; на сервере наоборот. Применяется перезапуском."),
    el("hr", "card-split"),
    speakOff, speechStatus, speechPickBox, speechActions,
    el("p", "field-hint",
      "⚠ Голосом агент отвечает в Telegram: в окне вложение пока приезжает строкой с путём к файлу, а не проигрывателем. " +
      "Синтез местный — текст ответа никуда не уходит. Применяется перезапуском."));

  void refresh();

  return {
    el: card("Голос", box,
      "Обе половины — ЗДЕСЬ, на процессоре: ни запись, ни текст ответа никуда не отправляются. Библиотеки едут в сборке, модель и голос качаются один раз."),
    enabled: () => enabled,
    model: () => model,
    keepLoaded: () => keep,
    speak: () => speak,
    speakVoice: () => speakVoice,
  };
}
