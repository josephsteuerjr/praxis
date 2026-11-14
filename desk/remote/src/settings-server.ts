import { t as uiText } from "../../lang/lang";
// Издание настроек Praxis: агент живёт НА СЕРВЕРЕ.
//
// ⚠ Что здесь есть и чего нет. Карточек модели, Telegram, режима, ограды,
// монтирования и управления компьютером здесь нет НИ ОДНОЙ — и это не пропуск,
// а суть приложения. Настройки агента правятся там, где он живёт: в его
// собственном конфиге на сервере. Отсюда они не читаются и туда не применяются.
//
// До 10.09 этих карточек не было тоже — но по-другому: они лежали в общем экране
// и прятались ветками `if (remote)`. Владелец 09.09 увидел, что бывает, когда
// заплатка чуть отстаёт от жизни: пустые поля модели и Telegram (их неоткуда
// взять), «модуль режима не нашёлся рядом с каналом», «Про управление
// компьютером харнесс ничего не рассказал», «Снимок устройства пуст», путь к
// пустой папке данных. Ни одно из этого не было поломкой: окно спрашивало у
// чужого харнесса то, чего у него и не должно быть.
//
// Теперь спрашивать нечему: этих карточек в сборке Praxis нет вовсе.
import QRCode from "qrcode";
import { el } from "../../ui-kit/window/lib";
import { card } from "../../ui-kit/dom";
import { serverAgentCard } from "./serveragent";
import { serverSpeechCard } from "../../ui-kit/window/server-speech";
import type { Config, Edition, EditionContext } from "../../ui-kit/window/views/settings-frame";

/** Одна карточка вместо семи: где живёт агент и что остаётся здесь. */
export async function serverEdition({ draft, saved }: EditionContext): Promise<Edition> {
  const base = String(saved.base || "").trim();
  const box = el("div");
  box.append(el("p", "field-hint",
    base
      ? uiText("ui.remote.src.settings.server.0d3576adb3", {p0: String(base)}) +
        uiText("ui.remote.src.settings.server.c8289557c1") +
        uiText("ui.remote.src.settings.server.14b1677c26")
      : uiText("ui.remote.src.settings.server.339b3e5e37")));
  box.append(el("p", "field-hint",
    uiText("ui.remote.src.settings.server.764b83c99f")));

  return {
    // ⚠ С 11.09 карточек две. Первая говорит, где живёт агент; вторая правит
    // ЕГО настройки — но не отсюда и не нашим файлом, а по каналу, в его
    // собственном конфиге на сервере (). Это не возвращение
    // карточек Элен: те правят файл рядом с окном через оболочку, которой здесь
    // нет вовсе.
    cards: [card(uiText("ui.remote.src.settings.server.3b2f8c23dd"), box), serverAgentCard().el, serverSpeechCard()],
    // Подпись у изданий разная: здесь имена — подписи ОКНА, а не агента.
    namesHint:
      uiText("ui.remote.src.settings.server.705d43e97f"),
    // Телефон подключается к СЕРВЕРУ: QR ведёт туда, а не на адрес этой машины,
    // куда телефону идти незачем. Правило брандмауэра здесь ни при чём.
    phoneBase: base,
    qrSvg: (text: string) =>
      QRCode.toString(text, { type: "svg", margin: 4, width: 240,
                              color: { dark: "#262320", light: "#ffffff" } }),
    note: () => "",
    collect(_out: Config): string {
      // Изданию к серверу дописывать в конфиг нечего: адрес, ключ и имена пишет каркас
      // (карточки «Перенос» и «Имена»), а настройки агента живут на сервере.
      // Пустой сбор — не заглушка, а объявленное «мне сюда писать нечего».
      void draft;
      return "";
    },
  };
}
