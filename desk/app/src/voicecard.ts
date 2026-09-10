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
  download: {
    state: string;
    model: string;
    note: string;
    got_bytes: number;
    total_bytes: number;
    updated_utc: string;
  } | null;
}

export interface VoiceCard {
  el: HTMLElement;
  enabled(): boolean;
  model(): string;
  keepLoaded(): boolean;
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
      status.textContent = state.library.why + ". Голос в этой поставке не поднимется.";
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

  box.append(onOff, status, pickBox, actions, keepOff,
    el("p", "field-hint",
      "Модель держится в памяти около 1,5 ГБ. Дома это дорого за несколько голосовых в день, поэтому по умолчанию " +
      "она загружается на время расшифровки и отпускается; на сервере наоборот. Применяется перезапуском."));

  void refresh();

  return {
    el: card("Голос", box,
      "Расшифровка идёт ЗДЕСЬ, на процессоре: запись никуда не отправляется. Библиотека едет в поставке, модель качается один раз."),
    enabled: () => enabled,
    model: () => model,
    keepLoaded: () => keep,
  };
}
