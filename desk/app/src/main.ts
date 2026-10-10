// Hélène — окно рядом с агентом.
//
// Само окно (полка, переписка, ход, состояние) общее обоим приложениям и живёт
// в `ui-kit/window/main.ts`. Здесь остаётся то, чем Элен отличается: агент живёт
// на ЭТОЙ машине, поэтому экран настроек несёт карточки местного агента — модель
// и ключ, Telegram, ограду рук со службой, монтирование, управление компьютером,
// папку данных. Перехватывать первый запуск незачем: харнесс рядом, адрес
// спрашивать не у кого.
import { start } from "../../ui-kit/window/main";
import { agentEdition } from "./settings-agent";
import { setAnimationPlayer } from '../../ui-kit/paper-media';
import QRCode from "qrcode";
import { setGuideQrRenderer } from "../../ui-kit/window/connection-guide";
setGuideQrRenderer(text=>QRCode.toString(text,{type:"svg",margin:4,width:240,color:{dark:"#262320",light:"#00000000"}}));
setAnimationPlayer(() => import('lottie-web/build/player/lottie_light').then(module => module.default));

start({ settingsEdition: agentEdition, productName: "Hélène" });
