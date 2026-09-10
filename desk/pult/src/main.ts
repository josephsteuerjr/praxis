// Пульт Praxis — то же окно, но агент живёт на сервере.
//
// Само окно (полка, переписка, ход, состояние) общее обоим приложениям и живёт
// в `ui-kit/window/main.ts`. Здесь остаётся то, чем Пульт отличается: экран
// настроек без карточек местного агента (его тут нет) и перехват первого
// запуска — адрес сервера спрашивается в окне, потому что установщика у этого
// варианта нет по замыслу.
import "../../ui-kit/window/styles/app.css";
import { start } from "../../ui-kit/window/main";
import { serverEdition } from "./settings-server";
import { askForServer } from "./first-run";

start({ settingsEdition: serverEdition, firstRun: askForServer, productName: "Praxis" });
