// Praxis — то же окно, но агент живёт на сервере.
//
// Само окно (полка, переписка, ход, состояние) общее обоим приложениям и живёт
// в `ui-kit/window/main.ts`. Здесь остаётся то, чем Praxis отличается: экран
// настроек без карточек местного агента (его тут нет) и перехват первого
// запуска — адрес сервера спрашивается в окне, потому что установщика у этого
// варианта нет по замыслу.
import "../../ui-kit/window/styles/app.css";
import { start } from "../../ui-kit/window/main";
import { serverEdition } from "./settings-server";
import { askForServer } from "./first-run";
import { setAnimationPlayer } from "../../ui-kit/paper-media";
import QRCode from "qrcode";
import { setGuideQrRenderer } from "../../ui-kit/window/connection-guide";
setGuideQrRenderer(text=>QRCode.toString(text,{type:"svg",margin:4,width:240,color:{dark:"#262320",light:"#00000000"}}));
setAnimationPlayer(() => import('lottie-web/build/player/lottie_light').then(module => module.default));

start({ settingsEdition: serverEdition, firstRun: askForServer,
        productName: "Praxis", localAgent: false });
